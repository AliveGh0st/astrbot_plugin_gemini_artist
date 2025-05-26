from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.api.all import *
from astrbot.api.message_components import *
import asyncio
from io import BytesIO
import time
import os
import random
from google import genai
from google.genai import types
from PIL import Image as PILImage
from google.genai.types import HttpOptions
from astrbot.core.utils.io import download_image_by_url
import re
import functools
from typing import List, Optional, Dict, Tuple
from collections import deque


@register("gemini_artist_plugin", "nichinichisou", "基于 Google Gemini 多模态模型的AI绘画插件", "1.1.0")
class GeminiArtist(Star):
    def __init__(self, context: Context, config: dict):
        super().__init__(context)
        
        self.config = config
        api_key_list_from_config = config.get("api_key", [])
        self.robot_id_from_config = config.get("robot_self_id", "")
        self.api_base_url_from_config = config.get("api_base_url", "https://generativelanguage.googleapis.com")
        self.model_name_from_config = config.get("model_name", "gemini-2.0-flash-exp")
        self.group_whitelist = config.get("group_whitelist", [])
        self.random_api_key_selection = config.get("random_api_key_selection", False)
        # self.wait_time_from_config = config.get("wait_time", 30) # LLM调用模式下可能不再需要

        # 新增：用户图片缓存，键为(user_id, session_id)，值为最近30张图片路径的双端队列
        self.user_image_cache: Dict[Tuple[str, str], deque] = {}
        self.max_cached_images = 30  # 每个用户最多缓存30张图片

        shared_data_path = "/AstrBot/data" 
        self.plugin_temp_base_dir = os.path.join(shared_data_path, "gemini_artist_temp")
        os.makedirs(self.plugin_temp_base_dir, exist_ok=True)
        self.temp_dir = self.plugin_temp_base_dir
        
        self.api_keys = [
            key.strip() 
            for key in api_key_list_from_config 
            if isinstance(key, str) and key.strip()
        ]
        self.current_api_key_index = 0
        
        if not self.api_keys:
            logger.warning("Gemini API密钥未配置或配置为空。插件可能无法正常工作。")

        # 定时清理相关配置
        self.cleanup_interval_seconds = self.config.get("temp_cleanup_interval_seconds", 3600 * 6) # 默认6小时
        self.cleanup_older_than_seconds = self.config.get("temp_cleanup_files_older_than_seconds", 86400 * 3) # 默认清理3天前的文件
        self._background_cleanup_task = None # 初始化后台任务为None

        if self.cleanup_interval_seconds > 0:
            self._background_cleanup_task = asyncio.create_task(self._periodic_temp_dir_cleanup())
            logger.info(f"GeminiArtist: 已启动定时清理任务，每隔 {self.cleanup_interval_seconds} 秒清理临时目录 {self.temp_dir} 中超过 {self.cleanup_older_than_seconds} 秒的文件。")
        else:
            logger.info("GeminiArtist: 定时清理功能已禁用 (temp_cleanup_interval_seconds <= 0)。")
    def _blocking_cleanup_temp_dir_logic(self, older_than_seconds: int):
        """实际执行清理的阻塞逻辑，方便被 to_thread 调用或直接在启动时调用。"""
        if not os.path.isdir(self.temp_dir):
            # logger.debug(f"临时目录 {self.temp_dir} 不存在。跳过清理逻辑。")
            return 0, 0 # 返回清理的文件数和错误数

        # logger.debug(f"执行临时目录清理逻辑: {self.temp_dir} (清理超过 {older_than_seconds} 秒的文件)")
        now = time.time()
        cleaned_count = 0
        error_count = 0

        try:
            for filename in os.listdir(self.temp_dir):
                file_path = os.path.join(self.temp_dir, filename)
                try:
                    if os.path.isfile(file_path) or os.path.islink(file_path):
                        file_mod_time = os.path.getmtime(file_path)
                        if (now - file_mod_time) > older_than_seconds:
                            os.remove(file_path)
                            # logger.debug(f"已移除旧的临时文件: {file_path}")
                            cleaned_count += 1
                except Exception as e_file:
                    # logger.error(f"清理临时文件 {file_path} 时出错: {e_file}")
                    error_count += 1
        except Exception as e_list:
            logger.error(f"列出目录 {self.temp_dir} 进行清理时出错: {e_list}")
            error_count +=1 # 将列目录错误也计为一次错误

    async def _periodic_temp_dir_cleanup(self):
        """后台定时任务，定期清理临时目录。"""
        while True:
            await asyncio.sleep(self.cleanup_interval_seconds)
            logger.info(f"定时清理任务触发，开始清理临时目录: {self.temp_dir}。")
            try:
                # 使用 asyncio.to_thread 在单独的线程中运行阻塞的I/O密集型清理逻辑
                # functools.partial 用于预先绑定参数给 _blocking_cleanup_temp_dir_logic
                cleanup_func = functools.partial(self._blocking_cleanup_temp_dir_logic, self.cleanup_older_than_seconds)
                await asyncio.to_thread(cleanup_func)
                # 对于 Python < 3.9, 可以考虑使用 loop.run_in_executor:
                # loop = asyncio.get_event_loop()
                # await loop.run_in_executor(None, cleanup_func) # None 表示使用默认的线程池执行器
            except asyncio.CancelledError:
                logger.info("定时清理任务已被取消。")
                break # 任务被取消，退出循环
            except Exception as e:
                logger.error(f"定时清理任务执行过程中发生错误: {e}", exc_info=True)
                # 即使发生错误，也应继续下一次调度，除非是 CancelledError


    # 新增：存储用户图片路径的方法
    def store_user_image(self, user_id: str, session_id: str, image_path: str) -> None:
        """存储用户发送的图片路径到缓存中"""
        key = (user_id, session_id)
        if key not in self.user_image_cache:
            self.user_image_cache[key] = deque(maxlen=self.max_cached_images)
        
        self.user_image_cache[key].append(image_path)
        logger.info(f"已存储用户 {user_id} 在会话 {session_id} 中的图片: {image_path}")

    # 新增：获取用户最近的图片路径
    def get_user_recent_image(self, user_id: str, session_id: str, index: int = 1) -> Optional[str]:
        """获取用户最近发送的第index张图片路径
        
        Args:
            user_id: 用户ID
            session_id: 会话ID（私聊为用户ID，群聊为群ID）
            index: 倒数第几张图片，默认为1（最新的图片）
            
        Returns:
            图片路径，如果没有找到则返回None
        """
        key = (user_id, session_id)
        if key not in self.user_image_cache or not self.user_image_cache[key]:
            return None
        
        images = list(self.user_image_cache[key])
        if index <= 0 or index > len(images):
            return None
        
        # 返回倒数第index张图片
        return images[-index]

    # 新增：监听所有图片消息并存储
    @filter.event_message_type(EventMessageType.ALL)
    async def cache_user_images(self, event: AstrMessageEvent):
        """监听所有消息，缓存用户发送的图片"""
        if not hasattr(event, 'message_obj') or not hasattr(event.message_obj, 'type'):
            return

        user_id = event.get_sender_id()
        
        # 忽略机器人自身消息
        if self.robot_id_from_config and user_id == self.robot_id_from_config:
            return
            
        session_id = user_id  # 默认私聊时 session_id 就是 user_id
        is_group_message = event.message_obj.type.name == 'GROUP_MESSAGE'

        if is_group_message:
            if hasattr(event.message_obj, 'group_id') and event.message_obj.group_id:
                session_id = event.message_obj.group_id
            else:
                return
        
        # 白名单检查
        if self.group_whitelist:
            identifier_to_check = session_id if is_group_message else user_id
            if str(identifier_to_check) not in [str(whitelisted_id) for whitelisted_id in self.group_whitelist]:
                return

        # 处理消息中的图片
        message_chain = event.get_messages()
        for msg in message_chain:
            if isinstance(msg, Image):
                try:
                    if hasattr(msg, 'url') and msg.url:
                        temp_img_path = await download_image_by_url(msg.url)
                        # 验证图片有效性
                        try:
                            img = PILImage.open(temp_img_path)
                            img.verify()  # 验证图片完整性
                            # 存储有效图片路径
                            self.store_user_image(user_id, session_id, temp_img_path)
                        except Exception as img_err:
                            logger.error(f"缓存图片验证失败: {temp_img_path}, 错误: {img_err}")
                except Exception as e:
                    logger.error(f"缓存用户图片失败: {str(e)}")

    @filter.llm_tool(name="gemini_draw")
    async def gemini_draw(self, event: AstrMessageEvent, prompt: str, image_index: int = 0) -> MessageEventResult:
        '''
        能够为用户生成图片的函数工具，你需要根据用户的要求（如图片要求、需要参考的图片）作为参数调用这个函数。你需要先回答你是否调用，再进行函数调用。

        Args:
            prompt (string): 用于生成图像的文本描述,此项为必填，此项为必填，此项为必填。
            image_index (number): 要使用的参考图片索引，0表示不使用参考图片，1表示最新的图片，2表示倒数第二张，以此类推。
        '''
        # 检查API密钥是否配置
        if not self.api_keys:
            yield event.plain_result("请联系管理员配置Gemini API密钥 (api_keys)")
            return

        # 确保message_obj存在且有type属性
        if not hasattr(event, 'message_obj') or not hasattr(event.message_obj, 'type'):
             logger.error(f"gemini_draw: 事件对象缺少 message_obj 或 type 属性: {type(event)}")
             yield event.plain_result("处理消息类型时出错，请联系管理员。")
             return

        user_id = event.get_sender_id()
        session_id = user_id  # 默认私聊时 session_id 就是 user_id
        is_group_message = event.message_obj.type.name == 'GROUP_MESSAGE'

        if is_group_message:
             if hasattr(event.message_obj, 'group_id') and event.message_obj.group_id:
                session_id = event.message_obj.group_id
             else:
                 logger.error(f"gemini_draw: 群聊消息但未找到群组ID: {event.message_obj.raw_message}")
                 yield event.plain_result("检测到群聊消息但未找到群组ID，无法启动绘制。")
                 return
        
        # 白名单检查
        if self.group_whitelist: 
            identifier_to_check = session_id if is_group_message else user_id
            if str(identifier_to_check) not in [str(whitelisted_id) for whitelisted_id in self.group_whitelist]:
                logger.info(f"gemini_draw: 用户/群组 {identifier_to_check} 不在白名单中，已忽略。")
                return

        # 忽略机器人自身消息
        if self.robot_id_from_config and user_id == self.robot_id_from_config:
            logger.debug(f"gemini_draw: 消息来自机器人自身 ({user_id})，已忽略。")
            return

        all_text = prompt.strip()
        all_images_pil = []

        # 处理参考图片
        if image_index > 0:
            reference_image_path = self.get_user_recent_image(user_id, session_id, image_index)
            if reference_image_path:
                try:
                    logger.info(f"gemini_draw: 使用参考图片: {reference_image_path}")
                    img = PILImage.open(reference_image_path)
                    img = img.convert("RGBA")  # 确保是RGBA格式
                    all_images_pil.append(img)
                except Exception as e:
                    logger.error(f"gemini_draw: 处理参考图片失败: {str(e)}")
                    yield event.plain_result(f"无法处理参考图片，请稍后再试。错误: {str(e)}")
                    return
            else:
                logger.warning(f"gemini_draw: 未找到索引为 {image_index} 的参考图片")
                yield event.plain_result(f"未找到索引为 {image_index} 的参考图片，请确保您已发送图片或索引正确。")
                return
        
        if not all_text and not all_images_pil:
            yield event.plain_result("请提供文本描述或参考图片。")
            return

        yield event.plain_result("正在处理您的绘图请求，请稍候...")
            
        try:
            logger.debug("gemini_draw: 调用 gemini_generate...")
            result = await self.gemini_generate(all_text, all_images_pil)
            logger.debug(f"gemini_draw: gemini_generate 调用完成。")

            if result is None:
                logger.error("gemini_draw: gemini_generate 返回 None!")
                yield event.plain_result("处理图片时发生严重内部错误（无法获取处理结果）。")
                return
            if not isinstance(result, dict):
                logger.error(f"gemini_draw: gemini_generate 返回非字典类型: {type(result)}")
                yield event.plain_result("处理图片时发生严重内部错误（结果格式错误）。")
                return

            text_response = result.get('text', '').strip()
            image_paths = result.get('image_paths', []) 
            logger.debug(f"gemini_generate 返回: 文本预览='{text_response[:100]}...', 图片数量={len(image_paths)}")

            if not text_response and not image_paths:
                logger.warning("gemini_draw: API未返回任何文本或图片内容。")
                yield event.plain_result("未能从API获取任何文本或图片内容。")
                return

            # 后续的图片发送逻辑 (BRANCH_SINGLE_MSG, BRANCH_NODES_MSG) 可以基本保持不变
            # ... (此处省略了与 collect_user_inputs 中相同的消息发送逻辑) ...
            # 您需要将 collect_user_inputs 方法中处理 result 并发送消息的部分复制到这里
            # 例如：
            if len(image_paths) < 2:
                # logger.debug(f"BRANCH_SINGLE_MSG: 图片数量 ({len(image_paths)}) < 2，准备发送普通消息。")
                chain = []
                if text_response:
                    chain.append(Plain(text_response))
                
                valid_image_count_for_chain = 0
                for idx, img_path in enumerate(image_paths):
                    # logger.debug(f"BRANCH_SINGLE_MSG: 检查图片 {idx+1}: '{img_path}'")
                    if img_path and os.path.exists(img_path) and os.path.getsize(img_path) > 0:
                        chain.append(Image.fromFileSystem(img_path))
                        valid_image_count_for_chain += 1
                        # logger.debug(f"BRANCH_SINGLE_MSG: 图片 {img_path} 有效并已添加。")
                    # else:
                        # logger.error(f"BRANCH_SINGLE_MSG: 图片文件无效或不存在: path='{img_path}', exists={os.path.exists(img_path) if img_path else 'N/A'}, size={os.path.getsize(img_path) if img_path and os.path.exists(img_path) else 'N/A'}")
                
                if chain:
                    # logger.info(f"BRANCH_SINGLE_MSG: 发送普通消息链 (有效图片数: {valid_image_count_for_chain}, 有文本: {bool(text_response)}). Chain: {[(type(c).__name__ + ':' + (c.text[:20] if hasattr(c,'text') else c.file if hasattr(c,'file') else 'UnknownComponent')) for c in chain]})
                    yield event.chain_result(chain)
                else:
                    # logger.warning("BRANCH_SINGLE_MSG: 构建普通消息链后内容为空。")
                    yield event.plain_result("抱歉，未能生成有效内容或图片处理失败。")
                return 

            # logger.info(f"BRANCH_NODES_MSG: 图片数量 ({len(image_paths)}) >= 2，准备构建合并转发消息。")
            bot_id_for_node = None; bot_name_for_node = None; bot_uin_source = "未确定"
            if hasattr(event, 'self_id') and event.self_id:
                try: bot_id_for_node = int(str(event.self_id).strip()); bot_uin_source = "event.self_id"
                except: logger.warning(f"event.self_id ('{event.self_id}') 转换失败"); bot_id_for_node = None
            if bot_id_for_node is None and self.robot_id_from_config:
                try: bot_id_for_node = int(str(self.robot_id_from_config).strip()); bot_uin_source = "config.robot_self_id"
                except: logger.warning(f"config.robot_self_id ('{self.robot_id_from_config}') 转换失败"); bot_id_for_node = None
            if bot_id_for_node is None:
                cfg_bot_id = str(self.config.get("bot_id", "")).strip()
                if cfg_bot_id:
                    try: bot_id_for_node = int(cfg_bot_id); bot_uin_source = "config.bot_id"
                    except: logger.warning(f"config.bot_id ('{cfg_bot_id}') 转换失败"); bot_id_for_node = None
            bot_name_for_node = str(self.config.get("bot_name", "绘图助手")).strip() or "绘图助手"

            if bot_id_for_node is None:
                logger.error(f"BRANCH_NODES_MSG: 无法确定有效的 bot_id 用于构建 Node 消息 (尝试来源: {bot_uin_source})。将尝试发送普通消息。")
                # 降级处理：如果无法获取bot_id，则尝试作为普通消息发送
                chain = []
                if text_response: chain.append(Plain(text_response))
                for img_path in image_paths:
                    if img_path and os.path.exists(img_path) and os.path.getsize(img_path) > 0:
                        chain.append(Image.fromFileSystem(img_path))
                if chain:
                    yield event.chain_result(chain)
                else:
                    yield event.plain_result("抱歉，未能生成有效内容或图片处理失败（尝试降级发送也失败）。")
                return

            nodes = []
            if text_response:
                nodes.append(Node(user_id=bot_id_for_node, nickname=bot_name_for_node, message_chain=[Plain(text_response)]))
            
            valid_image_count_for_nodes = 0
            for idx, img_path in enumerate(image_paths):
                # logger.debug(f"BRANCH_NODES_MSG: 检查图片 {idx+1} for Node: '{img_path}'")
                if img_path and os.path.exists(img_path) and os.path.getsize(img_path) > 0:
                    nodes.append(Node(user_id=bot_id_for_node, nickname=bot_name_for_node, message_chain=[Image.fromFileSystem(img_path)]))
                    valid_image_count_for_nodes += 1
                    # logger.debug(f"BRANCH_NODES_MSG: 图片 {img_path} 有效并已为 Node 添加。")
                # else:
                    # logger.error(f"BRANCH_NODES_MSG: 图片文件无效或不存在 (for Node): path='{img_path}', exists={os.path.exists(img_path) if img_path else 'N/A'}, size={os.path.getsize(img_path) if img_path and os.path.exists(img_path) else 'N/A'}")

            if nodes:
                # logger.info(f"BRANCH_NODES_MSG: 发送合并转发消息 (有效图片数: {valid_image_count_for_nodes}, 有文本: {bool(text_response)}). Node count: {len(nodes)}")
                yield event.node_custom_result(nodes)
            else:
                # logger.warning("BRANCH_NODES_MSG: 构建合并转发消息后内容为空。")
                yield event.plain_result("抱歉，未能生成有效内容或图片处理失败（尝试构建合并消息也失败）。")

        except types.StopCandidateException as e:
            logger.warning(f"Gemini API 请求因安全设置被阻止 (StopCandidateException): {e}")
            yield event.plain_result(f"请求内容可能违反了安全策略，已被阻止。{e}")
        except types.BlockedPromptException as e:
            logger.warning(f"Gemini API 请求因Prompt被阻止 (BlockedPromptException): {e}")
            yield event.plain_result(f"您的提示词可能包含不当内容，已被阻止。 {e}")
        except types.SafetyFeedbackError as e:
            logger.warning(f"Gemini API 请求因安全反馈错误 (SafetyFeedbackError): {e}")
            yield event.plain_result(f"请求因安全原因未能完成。{e}")
        except Exception as e:
            logger.error(f"gemini_draw: 调用 gemini_generate 或处理结果时发生未知错误: {e}", exc_info=True)
            yield event.plain_result(f"处理您的请求时发生意外错误: {str(e)}")

    async def gemini_generate(self, text_prompt: str, images_pil: list = None):
        """处理图片和文本，调用Gemini API，支持多密钥轮询和随机选择。"""
        # logger.critical(f"CRITICAL_PROCESS_LOG: self.temp_dir at start of gemini_generate: {self.temp_dir}")
        if not self.api_keys:
            # 此处应返回一个可由调用者处理的错误，或者直接 yield 错误消息
            # 但由于此函数被 collect_user_inputs 调用，后者会处理 yield
            # 因此这里可以直接 raise，由上层捕获或传递
            # logger.error("gemini_generate: 没有配置API密钥 (api_keys)")
            # return {'text': "错误：没有配置API密钥。", 'image_paths': []} # 或者返回错误信息
            raise ValueError("没有配置API密钥 (api_keys)")

        http_options = HttpOptions(
            base_url=self.api_base_url_from_config
        )

        max_retries = len(self.api_keys)
        last_exception = None
        
        # API Key 选择逻辑
        key_indices_to_try = list(range(len(self.api_keys)))
        if self.random_api_key_selection:
            random.shuffle(key_indices_to_try) # 随机打乱索引顺序
            # logger.info(f"gemini_generate: 启用随机API Key选择，尝试顺序: {key_indices_to_try}")
        else:
            # 按顺序轮询，从 current_api_key_index 开始
            key_indices_to_try = [(self.current_api_key_index + i) % len(self.api_keys) for i in range(len(self.api_keys))]
            # logger.info(f"gemini_generate: 禁用随机API Key选择，按顺序尝试，起始索引: {self.current_api_key_index}, 尝试顺序: {key_indices_to_try}")

        for attempt_num, key_idx_to_use in enumerate(key_indices_to_try):
            current_key_to_try = self.api_keys[key_idx_to_use]
            try:
                logger.info(f"gemini_generate: 尝试使用API密钥{key_idx_to_use+1} (尝试次数: {attempt_num + 1}/{max_retries})")
                client = genai.Client(
                    api_key=current_key_to_try,
                    http_options=http_options
                )

                contents = []
                if text_prompt:  # 修改 text 为 text_prompt
                    contents.append(text_prompt)
                for img in images_pil:  # 修改 images 为 images_pil
                    contents.append(img)

                if len(contents) == 2 and text_prompt and len(images_pil) == 1:  # 修改 text 为 text_prompt，images 为 images_pil
                    contents = (text_prompt, images_pil[0])

                if not contents:
                    # logger.warning("gemini_generate: 没有有效的内容可以发送给Gemini API")
                    # return {'text': "错误：没有提供任何内容。", 'image_paths': []}
                    raise ValueError("没有有效的内容可以发送给Gemini API")

                response = await asyncio.to_thread(
                    client.models.generate_content,
                    model="models/"+self.model_name_from_config,
                    contents=contents,
                    config=types.GenerateContentConfig(response_modalities=['Text', 'Image'])
                )

                # logger.info(f"gemini_generate: Gemini API响应 (使用密钥索引 {key_idx_to_use}): {response}")

                result = {'text': '', 'image_paths': []}

                if not response or not hasattr(response, 'candidates') or not response.candidates:
                    block_reason_msg = "未知原因"
                    if hasattr(response, 'prompt_feedback') and response.prompt_feedback.block_reason:
                         block_reason_msg = f"{response.prompt_feedback.block_reason_message or response.prompt_feedback.block_reason}"
                    # logger.warning(f"gemini_generate: Gemini API请求被阻止或返回空候选: {block_reason_msg}")
                    raise ValueError(f"Gemini API请求被阻止: {block_reason_msg}")

                if not hasattr(response.candidates[0], 'content') or not response.candidates[0].content:
                     # logger.warning("gemini_generate: Gemini API返回的content为空")
                     raise ValueError("Gemini API返回的content为空")

                if not hasattr(response.candidates[0].content, 'parts') or not response.candidates[0].content.parts:
                    # logger.warning("gemini_generate: Gemini API返回的parts为空")
                    raise ValueError("Gemini API返回的parts为空")

                for part in response.candidates[0].content.parts:
                    if hasattr(part, 'text') and part.text is not None:
                        result['text'] += part.text
                    elif hasattr(part, 'inline_data') and part.inline_data is not None:
                        img_data = part.inline_data.data
                        img = PILImage.open(BytesIO(img_data))
                        img = img.convert("RGBA")

                        # 确保临时目录存在 (理论上 __init__ 已创建，但再次检查无害)
                        os.makedirs(self.temp_dir, exist_ok=True) 
                        temp_file_path = os.path.join(self.temp_dir, f"gemini_result_{time.time()}_{key_idx_to_use}.png")
                        img.save(temp_file_path, format="PNG")
                        result['image_paths'].append(temp_file_path)
                
                # logger.debug(f"gemini_generate_RESULT_PRE_RETURN: Attempt {attempt_num + 1}, KeyIndex {key_idx_to_use}")
                # logger.debug(f"  Result Text Preview: '{result.get('text', '')[:100]}...'" )
                # logger.debug(f"  Result Image Paths Count: {len(result.get('image_paths', []))}")
                # logger.debug(f"  Result Image Paths List: {result.get('image_paths', [])}")
                # for idx, path in enumerate(result.get('image_paths', [])):
                    # logger.debug(f"    Image Path {idx}: '{path}', Exists: {os.path.exists(path) if path else False}, Size: {os.path.getsize(path) if path and os.path.exists(path) else 'N/A'}")
                
                # 如果成功，更新 current_api_key_index 以便下次轮询从下一个开始 (仅在非随机模式下)
                if not self.random_api_key_selection:
                    self.current_api_key_index = (key_idx_to_use + 1) % len(self.api_keys)
                return result

            except Exception as e:
                logger.error(f"gemini_generate: API处理失败 (使用密钥索引 {key_idx_to_use}): {str(e)}")
                last_exception = e
                # 在非随机模式下，如果当前密钥失败，则传统的 current_api_key_index 递增逻辑仍然适用，
                # 但由于我们是按 key_indices_to_try 列表尝试，所以不需要在这里更新 self.current_api_key_index
                # self.current_api_key_index = (self.current_api_key_index + 1) % len(self.api_keys)
                if attempt_num < max_retries - 1:
                    logger.info(f"gemini_generate: 将尝试下一个API密钥 (下一个尝试索引: {key_indices_to_try[attempt_num+1]})")
                else:
                    logger.error("gemini_generate: 所有API密钥均尝试失败。")

        if last_exception:
            # logger.error(f"gemini_generate_ALL_ATTEMPTS_FAILED: 所有API密钥尝试均失败。最后错误: {last_exception}")
            raise last_exception # 将最后遇到的异常向上抛出
        else:
            # 理论上不应该执行到这里，因为如果所有尝试都失败，last_exception 应该有值
            # 但作为保险，如果意外到达这里，也抛出错误
            logger.error("gemini_generate: 未知原因导致未能成功从API获取数据且无异常记录。")
            raise ValueError("Gemini API处理失败，且没有可用的API密钥或未记录明确错误。")


    async def terminate(self):
        '''插件被卸载/停用时调用，用于清理资源。'''
        logger.info("GeminiArtist: 正在执行 terminate 清理操作...")
        self.waiting_users.clear()
        self.user_inputs.clear() 

        # 取消后台清理任务
        if self._background_cleanup_task and not self._background_cleanup_task.done():
            logger.info("GeminiArtist: 正在取消后台定时清理任务...")
            self._background_cleanup_task.cancel()
            try:
                await self._background_cleanup_task
                logger.info("GeminiArtist: 后台定时清理任务已成功取消并结束。")
            except asyncio.CancelledError:
                logger.info("GeminiArtist: 后台定时清理任务捕获到 CancelledError，已正常终止。")
            except Exception as e_task_cancel:
                logger.error(f"GeminiArtist: 等待后台清理任务结束时发生异常: {e_task_cancel}", exc_info=True)
        else:
            logger.info("GeminiArtist: 无活动的后台清理任务需要取消，或任务已完成。")

        # 清理临时文件 (这里可以保留，作为最后一道防线，或者如果希望terminate时也执行一次清理)
        # 如果_periodic_temp_dir_cleanup能可靠运行，这里的清理可能不是必须的，除非希望立即清空
        logger.info(f"GeminiArtist: 尝试在 terminate 中执行一次最终的临时文件清理 ({self.temp_dir})...")
        try:
            # 可以选择在这里也调用阻塞清理逻辑，或者依赖定时任务的最后一次执行
            # 为了确保插件卸载时尽可能干净，可以再执行一次
            # 注意：如果 _blocking_cleanup_temp_dir_logic 依赖 self 的其他状态，需确保此时状态有效
            # 由于 _blocking_cleanup_temp_dir_logic 相对独立，这里直接调用通常是安全的
            cleaned_count, error_count = self._blocking_cleanup_temp_dir_logic(0) # 清理所有文件，无论时间
            logger.info(f"GeminiArtist: terminate 中的最终清理完成，移除了 {cleaned_count} 个文件，发生 {error_count} 个错误。")
        except Exception as e_final_cleanup:
            logger.error(f"GeminiArtist: terminate 中的最终清理失败: {e_final_cleanup}", exc_info=True)
        
        # 尝试移除插件自身的临时目录 (如果它是空的)
        if hasattr(self, 'temp_dir') and os.path.exists(self.temp_dir):
            if self.temp_dir == self.plugin_temp_base_dir: # 再次确认是插件自己的目录
                try:
                    # 仅当目录为空时 os.rmdir 才能成功
                    if not os.listdir(self.temp_dir): # 检查目录是否为空
                        os.rmdir(self.temp_dir)
                        logger.info(f"terminate: 已成功移除空的临时目录: {self.temp_dir}")
                    else:
                        logger.info(f"terminate: 临时目录 {self.temp_dir} 非空，未移除。")
                except OSError as e_rmdir:
                    logger.warning(f"terminate: 移除临时目录 {self.temp_dir} 失败: {e_rmdir}")
        else:
            logger.info("terminate: 插件临时目录未找到或未定义，无需移除操作。")
        logger.info("GeminiArtist: terminate 清理操作执行完毕。")
