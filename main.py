from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.api.all import *
from astrbot.api.message_components import Node, Plain, Image ,Nodes
import asyncio
from io import BytesIO
import time
import os
import random
from google import genai
from PIL import Image as PILImage
from google.genai.types import HttpOptions
from astrbot.core.utils.io import download_file
import functools
from typing import List, Optional, Dict, Tuple
from collections import deque
import base64
import time
from astrbot.core.conversation_mgr import Conversation
import json
from pathlib import Path



@register("gemini_artist_plugin", "nichinichisou", "基于 Google Gemini 多模态模型的AI绘画插件", "1.2.0")
class GeminiArtist(Star):
    def __init__(self, context: Context, config: dict):
        super().__init__(context)

        self.config = config
        api_key_list_from_config = config.get("api_key", [])
        self.api_base_url_from_config = config.get("api_base_url", "https://generativelanguage.googleapis.com")
        self.model_name_from_config = config.get("model_name", "gemini-2.0-flash-exp")
        self.group_whitelist = config.get("group_whitelist", [])
        self.robot_id_from_config = config.get("robot_self_id") 
        self.random_api_key_selection = config.get("random_api_key_selection", False)

        # 存储用户发送的图片URL缓存
        self.image_history_cache: Dict[Tuple[str, str], deque[Tuple[str, Optional[str]]]] = {}
        self.max_cached_images = self.config.get("max_cached_images", 5)

        # 设置插件的临时文件目录
        shared_data_path = Path(__file__).resolve().parent.parent.parent
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

        # 配置临时文件清理任务
        self.cleanup_interval_seconds = self.config.get("temp_cleanup_interval_seconds", 3600 * 6)
        self.cleanup_older_than_seconds = self.config.get("temp_cleanup_files_older_than_seconds", 86400 * 3)
        self._background_cleanup_task = None

        # 启动后台定时清理任务
        if self.cleanup_interval_seconds > 0:
            self._background_cleanup_task = asyncio.create_task(self._periodic_temp_dir_cleanup())
            logger.info(f"GeminiArtist: 已启动定时清理任务，每隔 {self.cleanup_interval_seconds} 秒清理临时目录 {self.temp_dir} 中超过 {self.cleanup_older_than_seconds} 秒的文件。")
        else:
            logger.info("GeminiArtist: 定时清理功能已禁用 (temp_cleanup_interval_seconds <= 0)。")

    def _blocking_cleanup_temp_dir_logic(self, older_than_seconds: int) -> Tuple[int, int]:
        """
        同步执行临时目录清理的逻辑，移除旧文件。
        """
        if not os.path.isdir(self.temp_dir):
            return 0, 0
        now, cleaned_count, error_count = time.time(), 0, 0
        try:
            for filename in os.listdir(self.temp_dir):
                file_path = os.path.join(self.temp_dir, filename)
                try:
                    if os.path.isfile(file_path) or os.path.islink(file_path):
                        if (now - os.path.getmtime(file_path)) > older_than_seconds:
                            os.remove(file_path)
                            cleaned_count += 1
                except Exception as e_file:
                    logger.error(f"清理临时文件 {file_path} 时出错: {e_file}")
                    error_count += 1
        except Exception as e_list:
            logger.error(f"列出目录 {self.temp_dir} 进行清理时出错: {e_list}")
            error_count += 1
        if cleaned_count > 0 or error_count > 0:
            logger.info(f"临时目录清理: 移除 {cleaned_count} 文件, 发生 {error_count} 错误 @ {self.temp_dir}")
        return cleaned_count, error_count

    async def _periodic_temp_dir_cleanup(self):
        """
        周期性地清理临时目录的后台任务。
        """
        while True:
            await asyncio.sleep(self.cleanup_interval_seconds)
            logger.info(f"定时清理触发: {self.temp_dir}")
            try:
                cleanup_func = functools.partial(self._blocking_cleanup_temp_dir_logic, self.cleanup_older_than_seconds)
                await asyncio.to_thread(cleanup_func)
            except asyncio.CancelledError:
                logger.info("定时清理任务已取消。")
                break
            except Exception as e:
                logger.error(f"定时清理任务出错: {e}", exc_info=True)

    def store_user_image(self, user_id: str, group_id: str, image_url: str, original_filename: Optional[str] = None) -> None:
        """
        将用户发送的图片URL存储到缓存中。
        """
        key = (user_id, group_id)
        if key not in self.image_history_cache:
            self.image_history_cache[key] = deque(maxlen=self.max_cached_images)
        self.image_history_cache[key].append((image_url, original_filename))
        logger.info(f"已存储用户 {user_id} group_id {group_id} 图片URL: {image_url} (缓存 {len(self.image_history_cache[key])}/{self.max_cached_images})")

    async def download_pil_image_from_url(self, image_url: str, context_description: str = "图片") -> Optional[PILImage.Image]:
        """
        从给定的URL下载图片并返回PIL Image对象。
        """
        logger.info(f"尝试使用 astrbot.core.utils.io.download_file 下载 {context_description} URL: {image_url}")

        # 尝试从URL中获取文件扩展名
        ext = ".png"
        try:
            path_part = image_url.split('?')[0].split('#')[0]
            base_name = os.path.basename(path_part)
            _, url_ext = os.path.splitext(base_name)
            if url_ext and url_ext.startswith('.') and len(url_ext) <= 5:
                ext = url_ext.lower()
            elif image_url.lower().endswith(('.jpg', '.jpeg', '.png', '.webp', '.gif')):
                for known_ext in ['.jpg', '.jpeg', '.png', '.webp', '.gif']:
                    if image_url.lower().endswith(known_ext):
                        ext = known_ext
                        break
        except Exception as e_ext:
            logger.debug(f"从URL {image_url} 获取扩展名时出错: {e_ext}，使用默认扩展名 {ext}")

        filename = f"gemini_artist_temp_{time.time()}_{random.randint(1000,9999)}{ext}"
        target_file_path = os.path.join(self.temp_dir, filename)

        os.makedirs(self.temp_dir, exist_ok=True)

        try:
            await download_file(url=image_url, path=target_file_path, show_progress=False)

            if os.path.exists(target_file_path) and os.path.isfile(target_file_path) and os.path.getsize(target_file_path) > 0:
                img_pil = PILImage.open(target_file_path)
                img_pil.load()
                if img_pil.mode != 'RGBA':
                    img_pil = img_pil.convert('RGBA')
                logger.info(f"图片从 {img_pil.mode} 转换为 RGBA 模式: {target_file_path}")


                logger.info(f"成功使用 download_file 下载并加载 {context_description} 从 {image_url} (本地文件: {target_file_path})")
                return img_pil
            else:
                logger.error(f"download_file 声称完成，但在路径 '{target_file_path}' 未找到有效文件或文件为空。URL: {image_url}")
                if os.path.exists(target_file_path):
                    try:
                        os.remove(target_file_path)
                    except Exception:
                        pass
                return None

        except FileNotFoundError:
            logger.error(f"尝试写入下载文件时发生 FileNotFoundError，请检查临时目录 '{self.temp_dir}' 是否有效且可写。 URL: {image_url}", exc_info=True)
            return None
        except PILImage.UnidentifiedImageError:
            logger.error(f"Pillow无法识别下载的图片文件 {target_file_path}。可能不是有效的图片格式或文件已损坏。 URL: {image_url}", exc_info=True)
            if os.path.exists(target_file_path):
                try:
                    os.remove(target_file_path)
                except Exception as e_rem:
                    logger.warning(f"清理无效下载文件 {target_file_path} 失败: {e_rem}")
            return None
        except Exception as e:
            logger.error(f"调用 download_file(url='{image_url}', path='{target_file_path}') 时发生错误: {type(e).__name__} - {e}", exc_info=True)
            if os.path.exists(target_file_path) and os.path.getsize(target_file_path) == 0:
                try:
                    os.remove(target_file_path)
                except Exception:
                    pass
            return None
    # def image_to_base64_data_url(self, image_path: str) -> str:
    #     """
    #     将图片文件直接转换为 base64 data URL，不进行任何压缩或优化。
    #     """
    #     mime_type = "image/jpeg"
    #     output_format_pil = "jpeg" # Pillow 保存时使用的格式名

    #     try:
    #         # 1. 使用 PIL 打开原始 PNG 图片
    #         with PILImage.open(image_path) as img_pil:
    #             # buffer = io.BytesIO()
    #             save_dir = os.path.join(self.temp_dir, f"gemini_base64_{time.time()}_{random.randint(100,999)}.png")
    #             width, height = img_pil.size
    #             # save_options = {
    #             #     'format': output_format_pil,
    #             #     'optimize': True,
    #             #     'compress_level': 7 # 调整压缩级别 (0-9)，7 通常是不错的平衡
    #             # }
    #             new_width = 200
    #             ratio = new_width / width
    #             new_height = int(height * ratio)
    #             new_size = (new_width, new_height)
    #             resized_img = img_pil.resize(new_size, PILImage.LANCZOS)
    #             buffer = BytesIO()
    #             resized_img.convert("RGB").save(buffer, format="JPEG", quality=95)  # quality可调
    #             # jpeg_base64 = base64.b64encode(buffer.getvalue()).decode("utf-8")
    #             logger.debug(f"BytesIO buffer 已创建。")

    #             try:
    #                 logger.debug(f"尝试将 resized_img (类型: {type(resized_img)}, 模式: {resized_img.mode}, 尺寸: {resized_img.size}) 保存到 buffer，格式: {output_format_pil}")
    #                 # resized_img.save(buffer, format=output_format_pil)
    #                 # 在调用 getvalue() 之前，可以检查 buffer 指针的位置
    #                 buffer_position_after_save = buffer.tell()
    #                 logger.debug(f"resized_img.save(buffer) 执行完毕。Buffer 当前指针位置: {buffer_position_after_save}")
    #                 if buffer_position_after_save == 0:
    #                     logger.warning(f"警告: resized_img.save(buffer) 后，buffer 指针仍在0，可能未写入数据。图片: {image_path}")
    #             except Exception as save_to_buffer_err:
    #                 logger.error(f"Pillow save to buffer 失败 for {image_path}: {save_to_buffer_err}", exc_info=True)
    #                 return None

    #             image_bytes = buffer.getvalue()
    #             logger.debug(f"buffer.getvalue() 执行完毕。获取到的 image_bytes 长度: {len(image_bytes)}")

    #             if not image_bytes:
    #                 logger.error(f"从 buffer 获取的 image_bytes 为空 (长度为0)。图片路径: {image_path}. Buffer 指针位置: {buffer.tell()}")
    #                 return None

    #             try:
    #                 encoded_string = base64.b64encode(image_bytes).decode('utf-8')
    #                 logger.debug(f"Base64编码完成。Encoded_string 长度: {len(encoded_string)}")
    #             except Exception as b64_err:
    #                 logger.error(f"Base64编码或解码失败: {b64_err}", exc_info=True)
    #                 return None


    #             if not encoded_string: # 理论上，如果 image_bytes 非空，这里不应该为空
    #                 logger.error(f"Base64编码后 encoded_string 仍然为空。图片路径: {image_path}, image_bytes长度: {len(image_bytes)}")
    #                 return None

    #             final_url = f"data:{mime_type};base64,{encoded_string}"
    #             logger.info(f"图片 {image_path} 已成功转换为Base64，输出URL前缀: {final_url[:70]}...")
    #             return final_url

    #     except FileNotFoundError:
    #         logger.error(f"Base64转换：文件未找到在最外层try-except: {image_path}")
    #         return None
    #     except PILImage.UnidentifiedImageError:
    #         logger.error(f"Base64转换：Pillow无法识别图片文件 (最外层try-except) {image_path}", exc_info=True)
    #         return None
    #     except Exception as e:
    #         logger.error(f"image_to_base64_data_url 中发生未知错误 (最外层try-except) ({image_path}): {e}", exc_info=True)
    #         return None

  
    # async def add_response_to_conversation(self,text: str, image_paths: List[str], event: AstrMessageEvent):
    #     """
    #     将生成的内容添加到当前对话上下文中。
    #     """
    #     text_response = text
    #     image_paths = image_paths
    #     uid = event.unified_msg_origin
    #     conversation_manager = self.context.conversation_manager
    #     curr_cid = await conversation_manager.get_curr_conversation_id(uid)
    #     conversation = await self.context.conversation_manager.get_conversation(uid, curr_cid)
        # current_history_list = []
        # # if conversation.history:
        # #     try:
        # #         loaded_history = json.loads(conversation.history)
        # #         if isinstance(loaded_history, list):
        # #             current_history_list = loaded_history
        # #         else:
        # #             # self.logger.warning(f"对话历史格式不正确 (非列表) for {uid}/{curr_cid}，将重置为空列表。")
        # #             print(f"对话历史格式不正确 (非列表) for {uid}/{curr_cid}，将重置为空列表。")
        # #     except json.JSONDecodeError:
        # #         # self.logger.error(f"解析对话历史失败 for {uid}/{curr_cid}，将重置为空列表。History: {conversation.history[:100]}...")
        # #         print(f"解析对话历史失败 for {uid}/{curr_cid}，将重置为空列表。")


        # # --- 3. 格式化插件的输出为助手消息 ---
        # user_message_content_parts = []

        # # 处理文本部分
        # if text_response: # 确保 text_response 是你插件生成的文本
        #     user_message_content_parts.append({
        #         "type": "text",
        #         "text": "我调用了你的生图功能，但是你无法查看到内容，为了接下来的对话，我将生成的图片发送给你"
        #     })

        # # 处理图片部分
        # for img_path in image_paths: # 确保 image_paths 是你插件生成的图片路径列表
        #     if img_path and os.path.exists(img_path) and os.path.getsize(img_path) > 0:
        #         base64_url = self.image_to_base64_data_url(img_path)
        #         # if base64_url:
        #         user_message_content_parts.append({
        #             "type": "image_url",
        #             "image_url": {"url": base64_url}
        #         })

        # # if user_message_content_parts: # 确保有内容可添加
        # #     # 如果有图片或混合内容，content是列表
        # #     final_assistant_content: str | list
        # #     if len(user_message_content_parts) == 1 and user_message_content_parts[0]["type"] == "text":
        # #         final_assistant_content = user_message_content_parts[0]["text"]
        # #     else:
        # #         final_assistant_content = user_message_content_parts
        #     new_uesr_message = {
        #         "role": "user",
        #         "content": user_message_content_parts
        #     }
        #     new_assistant_message = {
        #         "role": "assistant",
        #         "content": "好的"
        #     }

        #     # --- 4. 追加到历史列表 ---
        #     current_history_list.append(new_uesr_message)
        #     current_history_list.append(new_assistant_message)

        #     # --- 5. 更新对话对象 ---
        #     existing_history = json.loads(conversation.history)
        #     merged_history = existing_history+current_history_list
        #     conversation.history = json.dumps(merged_history, ensure_ascii=False)
        #     conversation.updated_at = int(time.time())

        #     # --- 6. 持久化对话 ---
        #     try:
        #         # BaseDatabase 有一个 `update_conversation` 方法，接收整个 Conversation 对象
        #             await conversation_manager.db.update_conversation(uid,curr_cid,conversation.history)
        #             # self.logger.info(f"对话 {curr_cid} 历史已更新。")
        #             print(f"对话 {curr_cid} 历史已更新。")
        #     except Exception as e:
        #         # self.logger.error(f"持久化对话历史失败 for {uid}/{curr_cid}: {e}")
        #         print(f"持久化对话历史失败 for {uid}/{curr_cid}: {e}")
        


    async def get_user_recent_image_pil_from_cache(self, user_id: str, group_id: str, index: int = 1) -> Optional[PILImage.Image]:
        """
        从用户图片缓存中获取指定索引的图片并下载为PIL Image对象。
        """
        key = (user_id, group_id)
        if key not in self.image_history_cache or not self.image_history_cache[key]:
            logger.debug(f"缓存中未找到用户 {user_id} group_id {group_id} 的图片URL。")
            return None
        cached_items = list(self.image_history_cache[key])
        if not (0 < index <= len(cached_items)):
            logger.debug(f"请求的图片URL索引 {index} 超出用户 {user_id} group_id {group_id} 缓存范围 ({len(cached_items)} 条)。")
            return None
        image_ref_str, _ = cached_items[-index]
        if image_ref_str.startswith("data:image"):
            logger.info(f"从缓存加载Base64 Data URL (用户 {user_id}, 上下文 {group_id}, 索引 {index})")
            try:
                header, encoded = image_ref_str.split(",", 1)
                image_bytes = base64.b64decode(encoded)
                pil_image = PILImage.open(BytesIO(image_bytes))
                return pil_image.convert('RGBA') if pil_image.mode != 'RGBA' else pil_image
            except Exception as e:
                logger.error(f"从缓存的Data URL解码图片失败: {e}")
                return None
        elif image_ref_str.startswith("http://") or image_ref_str.startswith("https://"):
            logger.info(f"从缓存加载HTTP URL并下载 (用户 {user_id}, 上下文 {group_id}, 索引 {index}): {image_ref_str}")
            return await self.download_pil_image_from_url(image_ref_str, f"缓存图片 (HTTP)")
        elif os.path.exists(image_ref_str): # 假设是本地文件路径
             logger.info(f"从缓存加载本地文件路径 (用户 {user_id}, 上下文 {group_id}, 索引 {index}): {image_ref_str}")
             try:
                pil_image = PILImage.open(image_ref_str)
                return pil_image.convert('RGBA') if pil_image.mode != 'RGBA' else pil_image
             except Exception as e:
                logger.error(f"从缓存的本地路径加载图片失败: {e}")
                return None
        else:
            logger.warning(f"缓存中的图片引用格式未知或无效: {image_ref_str[:100]}...")
            return None

    @filter.event_message_type(EventMessageType.ALL)
    async def cache_user_images(self, event: AstrMessageEvent):
        """
        监听所有消息，将用户发送的图片URL缓存起来。
        """
        if not hasattr(event, 'message_obj') or not hasattr(event.message_obj, 'type'):
            return
        user_id = event.get_sender_id()
        if self.robot_id_from_config and user_id == self.robot_id_from_config:
            return
        group_id = event.message_obj.group_id
        if self.group_whitelist:
            identifier_to_check = event.message_obj.group_id if event.message_obj.group_id else user_id
            if str(identifier_to_check) not in [str(whitelisted_id) for whitelisted_id in self.group_whitelist]:
                return
        if group_id == "":
            group_id = user_id
            logger.debug(f"收到来自用户 {user_id} group_id {group_id} 的消息。")
        for msg_component in event.get_messages():
            if isinstance(msg_component, Image) and hasattr(msg_component, 'url') and msg_component.url:
                self.store_user_image(user_id, group_id, msg_component.url, getattr(msg_component, 'file', None))

    @filter.llm_tool(name="gemini_draw")
    async def gemini_draw(self, event: AstrMessageEvent, prompt: str, image_index: int = 0, reference_bot: bool = False) -> str:
        '''
        图像生成、修改、处理工具，调用关键词“生成”、“图像处理”、“画”等。
        Args:
            prompt (string): 图像的文本描述。需要包含“生成”、“图片”等关键词。
            image_index (number, optional): 要使用的来自用户历史记录的倒数几张图片作为参考。默认为0 (不使用)。
                                            1表示最新的1张图片，2表示最新的2张，以此类推。
            reference_bot (boolean, optional): 是否参考的是你先前生成的图片(与来自与你聊天记录不同), 默认为 False。
        '''
        if not self.api_keys:
            yield event.plain_result("请联系管理员配置Gemini API密钥。")
            return
        if not hasattr(event, 'message_obj') or not hasattr(event.message_obj, 'type'):
            logger.error(f"gemini_draw: 事件对象缺少 message_obj 或 type 属性。")
            yield event.plain_result("处理消息时出错。")
            return

        command_sender_id = event.get_sender_id()
        group_id = event.message_obj.group_id

        if self.group_whitelist and str(event.message_obj.group_id or command_sender_id) not in map(str, self.group_whitelist):
            return
        if self.robot_id_from_config and command_sender_id == self.robot_id_from_config:
            return

        all_text = prompt.strip()
        all_images_pil: List[PILImage.Image] = []

        # 优先处理回复消息中的图片
        replied_image_pil: Optional[PILImage.Image] = None
        message_chain = event.get_messages()

        for msg_component in message_chain:
            if isinstance(msg_component, Reply):
                logger.debug(f"检测到回复消息。尝试解析被引用的图片。Reply component dir: {dir(msg_component)}")
                if hasattr(msg_component, '__dict__'):
                    logger.debug(f"Reply component vars: {vars(msg_component)}")

                source_chain: Optional[List[MessageComponent]] = None
                # 尝试从回复消息中获取图片链
                if hasattr(msg_component, 'chain') and isinstance(msg_component.chain, list):
                    source_chain = msg_component.chain
                    logger.debug("Reply component has 'chain' attribute.")
                elif hasattr(msg_component, 'message') and isinstance(msg_component.message, list):
                    source_chain = msg_component.message
                    logger.debug("Reply component has 'message' attribute (list).")
                elif hasattr(msg_component, 'source') and hasattr(msg_component.source, 'message_chain') and isinstance(msg_component.source.message_chain, list):
                    source_chain = msg_component.source.message_chain
                    logger.debug("Reply component has 'source.message_chain' attribute.")

                if source_chain:
                    for replied_part in source_chain:
                        if isinstance(replied_part, Image) and hasattr(replied_part, 'url') and replied_part.url:
                            replied_image_pil = await self.download_pil_image_from_url(replied_part.url, "直接引用的消息中的图片")
                            if replied_image_pil:
                                logger.info("成功从直接引用的消息中加载了图片作为参考。")
                                all_images_pil.append(replied_image_pil)
                if replied_image_pil:
                    logger.info("使用直接引用的图片作为唯一参考，忽略 image_index 和 reference_user_id。")
                    image_index = 0
                    reference_bot = False
                    break
                break

        # 如果没有直接引用的图片，且指定了图片索引，则尝试从缓存中获取
        if not all_images_pil and image_index > 0:
            num_images_to_fetch = image_index
            if reference_bot == True:
                user_id_for_cache_lookup = self.robot_id_from_config
            else:
                user_id_for_cache_lookup = command_sender_id
            group_id_for_cache_lookup = event.message_obj.group_id or command_sender_id
            logger.info(f"尝试从用户 {user_id_for_cache_lookup} (上下文 {group_id_for_cache_lookup}) 缓存获取最新的 {num_images_to_fetch} 张图片。")

            key_for_cache = (user_id_for_cache_lookup, group_id_for_cache_lookup)
            if key_for_cache not in self.image_history_cache or not self.image_history_cache[key_for_cache]:
                message = f"缓存中未找到用户 {user_id_for_cache_lookup} (上下文 {group_id_for_cache_lookup}) 的图片历史。"
                logger.warning(message)

            
            cached_items_list = list(self.image_history_cache[key_for_cache])
            # 确保不要请求超过缓存数量的图片
            actual_num_to_fetch = min(num_images_to_fetch, len(cached_items_list))

            if actual_num_to_fetch == 0:
                message = f"用户 {user_id_for_cache_lookup} (上下文 {group_id_for_cache_lookup}) 的图片历史为空，无法按数量 {num_images_to_fetch} 获取参考图。"
                logger.warning(message)


            fetched_count = 0
            # 从最新的开始获取 (倒数第1, 倒数第2, ..., 倒数第 actual_num_to_fetch)
            for i in range(1, actual_num_to_fetch + 1):
                pil_image_from_cache = await self.get_user_recent_image_pil_from_cache(
                    user_id_for_cache_lookup,
                    group_id_for_cache_lookup,
                    i 
                )
                if pil_image_from_cache:
                    all_images_pil.append(pil_image_from_cache)
                    fetched_count +=1
                else:
                    logger.warning(f"未能加载用户 {user_id_for_cache_lookup} (上下文 {session_id_for_cache_lookup}) 的倒数第 {i} 张图片。")
            
            if fetched_count == 0 and num_images_to_fetch > 0 : # 如果指定要图但一张都没取到
                message = f"尝试获取最新的 {num_images_to_fetch} 张图片，但未能成功加载任何一张。"
                logger.warning(message)
            
            logger.info(f"成功从缓存加载了 {fetched_count} 张参考图片。")

        if not all_text and not all_images_pil:
            event.plain_result("请提供文本描述，或通过回复图片/指定图片索引及可选的参考用户来提供有效的参考图片。")
            event.stop_event()

        yield event.plain_result("正在生成图片，请稍候...")

        try:
            logger.debug(f"gemini_draw: 调用 gemini_generate (文本: '{all_text[:50]}...', PIL图片数: {len(all_images_pil)})")
            result = await self.gemini_generate(all_text, all_images_pil)
            logger.debug(f"gemini_draw: gemini_generate 调用完成。")

            if result is None or not isinstance(result, dict):
                logger.error(f"gemini_draw: gemini_generate 返回无效结果: {type(result)}")
                yield event.plain_result("处理图片时发生内部错误。")
                stop_event()

            text_response = result.get('text', '').strip()
            image_paths = result.get('image_paths', [])
            logger.debug(f"gemini_generate 返回: 文本预览='{text_response[:100]}...', 生成图片数={len(image_paths)}")
            if image_paths:
                logger.info(f"准备缓存 {len(image_paths)} 张Gemini生成的图片路径...")
            for i, img_path in enumerate(image_paths):
                if os.path.exists(img_path):
                    # 缓存本地文件路径。command_sender_id 是触发这次生成的用户。
                    # current_session_context_id 是图片生成的上下文（群或私聊）。
                    self.store_user_image(
                        command_sender_id, # 图片归属于触发操作的用户
                        group_id, # 在当前会话上下文中
                        img_path, # 缓存的是本地文件路径
                        f"gemini_generated_{i+1}_{os.path.basename(img_path)}"
                    )
                else:
                    logger.warning(f"Gemini生成的图片路径无效，无法缓存或发送: {img_path}")
            if not text_response and not image_paths:
                logger.warning("gemini_draw: API未返回任何文本或生成的图片内容。")
                yield event.plain_result("未能从API获取任何内容。")
                event.stop_event()

            # 如果只有一张图片或没有图片，则直接发送
            if len(image_paths) < 2:
                chain = []
                # if text_response:
                #     chain.append(Plain(text_response))
                for img_path in image_paths:
                    if img_path and os.path.exists(img_path) and os.path.getsize(img_path) > 0:
                        chain.append(Image.fromFileSystem(img_path))
                if chain:
                    # await self.add_response_to_conversation(text_response, image_paths, event)
                    # image_urls_for_llm_request = []
                    # for img_path in image_paths:
                    #     if img_path and os.path.exists(img_path) and os.path.getsize(img_path) > 0:
                    #         image_urls_for_llm_request.append(self.image_to_base64_data_url(img_path))
                    tool_output_data = {
                        "generated_text": text_response,
                        "number_of_images_generated": len(image_paths),
                        "user_instruction_for_llm": (f"你生成了 {len(image_paths)} 张图片"
                    "这些图片已经发送给用户，并且用户可以通过图片索引（例如，image_index=1 代表最新生成的这张/这些图片）来引用它们进行后续操作。"
                    "请根据用户的原始意图和这些新生成的图文内容继续对话。" )
                        }
                            # 工具的返回值应该是这个JSON字符串
                            # 当LLM调用此工具后，这个字符串会作为工具结果进入LLM的上下文
                    yield json.dumps(tool_output_data, ensure_ascii=False)
                    yield event.chain_result(chain)
                else:
                    if text_response:
                        yield event.plain_result(text_response)
                    else:
                        yield event.plain_result("抱歉，未能生成有效内容。")
                return
            else:
            # 如果有多张图片，尝试以合并转发消息的形式发送
                bot_id_for_node_str = event.message_obj.self_id or self.robot_id_from_config or self.config.get("bot_id")
                bot_id_for_node = int(str(bot_id_for_node_str).strip()) if bot_id_for_node_str and str(bot_id_for_node_str).strip().isdigit() else None
                if bot_id_for_node is None:
                    logger.error(f"gemini_draw: 无法确定有效的 bot_id。尝试普通发送。")
                    chain = []
                    if text_response:
                        chain.append(Plain(text_response))
                    for img_path in image_paths:
                        if img_path and os.path.exists(img_path) and os.path.getsize(img_path) > 0:
                            chain.append(Image.fromFileSystem(img_path))
                    if chain:
                        yield event.chain_result(chain)
                    else:
                        yield event.plain_result("抱歉，未能生成有效内容。")
                    return

                bot_name_for_node = str(self.config.get("bot_name", "绘图助手")).strip() or "绘图助手"
                ns = Nodes([])
                if text_response:
                    paragraphs = text_response.split('\n\n')
                if paragraphs:
                    ns.nodes.append(Node(
                            user_id=bot_id_for_node,nickname=bot_name_for_node,content=[Plain(paragraphs[0])]
                        ))
                for idx, img_path in enumerate(image_paths): 
                    if img_path and os.path.exists(img_path) and os.path.getsize(img_path) > 0:
                        if len(paragraphs) == 1:
                            content = [Plain(""), Image.fromFileSystem(img_path)]
                        elif idx < len(paragraphs):
                            content = [Plain(paragraphs[idx+1]), Image.fromFileSystem(img_path)]
                        else:
                            content = [Plain(""), Image.fromFileSystem(img_path)]
                        
                        ns.nodes.append(Node(
                            user_id=bot_id_for_node,
                            nickname=bot_name_for_node,
                            content=content
                        ))
                if ns:
                    tool_output_data = {
                        "generated_text": text_response,
                        "number_of_images_generated": len(image_paths),
                        "user_instruction_for_llm": (f"你生成了 {len(image_paths)} 张图片"
                    "这些图片已经发送给用户，并且用户可以通过图片索引（例如，image_index=1 代表最新生成的这张/这些图片）来引用它们进行后续操作。"
                    "请根据用户的原始意图和这些新生成的图文内容继续对话。")
                    }
                    yield json.dumps(tool_output_data, ensure_ascii=False)
                    yield event.chain_result([ns])
                else:
                    yield event.plain_result("抱歉，未能生成有效内容。")

        except Exception as e:
            logger.error(f"gemini_draw 未知错误: {e}", exc_info=True)
            yield event.plain_result(f"处理请求时发生意外错误: {str(e)}")

    async def gemini_generate(self, text_prompt: str, images_pil: List[PILImage.Image] = None):
        """
        调用Gemini API生成文本和图片。
        支持多API密钥轮询和随机选择。
        """
        if not self.api_keys:
            raise ValueError("没有配置API密钥 (api_keys)")
        images_pil = images_pil or []
        http_options = HttpOptions(base_url=self.api_base_url_from_config)
        max_retries, last_exception = len(self.api_keys), None
        key_indices_to_try = list(range(len(self.api_keys)))
        if self.random_api_key_selection:
            random.shuffle(key_indices_to_try)
        else:
            key_indices_to_try = [(self.current_api_key_index + i) % len(self.api_keys) for i in range(len(self.api_keys))]

        for attempt_num, key_idx_to_use in enumerate(key_indices_to_try):
            current_key_to_try = self.api_keys[key_idx_to_use]
            try:
                logger.info(f"gemini_generate: 尝试API密钥索引 {key_idx_to_use} (尝试 {attempt_num + 1}/{max_retries})")
                client = genai.Client(api_key=current_key_to_try, http_options=http_options)
                contents = []
                if text_prompt:
                    contents.append(text_prompt+",请使用中文回复,文字段与图片对应")
                for img_item in images_pil:
                    contents.append(img_item)
                if not contents:
                    raise ValueError("没有有效的内容发送给Gemini API")

                response = await asyncio.to_thread(
                    client.models.generate_content,
                    model="models/" + self.model_name_from_config,
                    contents=contents,
                    config=genai.types.GenerateContentConfig(response_modalities=['Text', 'Image'])
                )
                result = {'text': '', 'image_paths': []}
                if not response:
                    logger.warning("gemini_generate: API响应为空。")
                    raise ValueError("Gemini API返回空响应。")


                if not hasattr(response, 'candidates') or not response.candidates:
                    logger.warning("gemini_generate: API响应中无候选。")
                    raise ValueError("Gemini API响应中无有效候选。")

                candidate = response.candidates[0]
                if hasattr(candidate, 'finish_reason') and candidate.finish_reason.name == 'SAFETY':
                    s_info = f" 安全评级: {candidate.safety_ratings}" if hasattr(candidate, 'safety_ratings') else ""
                    msg = f"内容因安全策略被阻止 (finish_reason: SAFETY).{s_info}"
                    logger.warning(f"gemini_generate: {msg}")
                    raise genai.types.SafetyFeedbackError(msg)

                if not (hasattr(candidate, 'content') and candidate.content and hasattr(candidate.content, 'parts') and candidate.content.parts):
                    f_info = f"(finish_reason: {candidate.finish_reason.name})" if hasattr(candidate, 'finish_reason') else ""
                    logger.warning(f"gemini_generate: Candidate content/parts为空 {f_info}.")
                    raise ValueError(f"Gemini API返回候选内容或部分为空 {f_info}.")

                for part in candidate.content.parts:
                    if hasattr(part, 'text') and part.text is not None:
                        result['text'] += part.text
                    elif hasattr(part, 'inline_data') and part.inline_data and hasattr(part.inline_data, 'mime_type') and part.inline_data.mime_type.startswith('image/'):
                        img_data = part.inline_data.data
                        gen_img = PILImage.open(BytesIO(img_data))
                        ext = part.inline_data.mime_type.split('/')[-1]
                        if ext not in ['png', 'jpeg', 'jpg', 'webp', 'gif']:
                            ext = 'png'
                        os.makedirs(self.temp_dir, exist_ok=True)
                        temp_fp = os.path.join(self.temp_dir, f"gemini_gen_{time.time()}_{random.randint(100,999)}.{ext}")
                        gen_img.save(temp_fp)
                        result['image_paths'].append(temp_fp)
                        logger.info(f"Gemini API 生成并保存图片: {temp_fp} (MIME: {part.inline_data.mime_type})")

                if not result['text'] and not result['image_paths']:
                    logger.warning(f"Gemini API返回空文本和图片. Candidate: {candidate}")
                if not self.random_api_key_selection:
                    self.current_api_key_index = (key_idx_to_use + 1) % len(self.api_keys)
                return result
            except Exception as e:
                logger.error(f"gemini_generate: API处理失败 (密钥 {key_idx_to_use}): {str(e)}", exc_info=True)
                last_exception = e

            if attempt_num < max_retries - 1:
                logger.info(f"gemini_generate: 尝试下个API密钥 (下个索引: {key_indices_to_try[attempt_num+1]})")
            else:
                logger.error("gemini_generate: 所有API密钥均尝试失败。")
        if last_exception:
            raise last_exception
        logger.error("gemini_generate: 未能从API获取数据且无明确异常。")
        raise ValueError("Gemini API处理失败，无可用密钥或未记录错误。")

    async def terminate(self):
        """
        插件终止时执行清理操作，包括清空图片缓存和取消后台清理任务。
        """
        logger.info("GeminiArtist: 执行 terminate 清理...")
        if hasattr(self, 'image_history_cache'):
            self.image_history_cache.clear()
            logger.info("用户图片URL缓存已清空。")
        if self._background_cleanup_task and not self._background_cleanup_task.done():
            logger.info("取消后台定时清理任务...")
            self._background_cleanup_task.cancel()
            try:
                await self._background_cleanup_task
            except asyncio.CancelledError:
                logger.info("后台清理任务已取消。")
            except Exception as e:
                logger.error(f"等待后台清理任务结束时异常: {e}", exc_info=True)
        else:
            logger.info("无活动后台清理任务或已完成。")
        logger.info(f"最终临时文件清理 ({self.temp_dir})...")
        try:
            await asyncio.to_thread(self._blocking_cleanup_temp_dir_logic, 0)
        except Exception as e:
            logger.error(f"最终清理失败: {e}", exc_info=True)
        # 仅当临时目录是插件特有的且为空时才尝试移除
        if hasattr(self, 'temp_dir') and os.path.exists(self.temp_dir) and self.temp_dir == self.plugin_temp_base_dir:
            try:
                if not os.listdir(self.temp_dir):
                    os.rmdir(self.temp_dir)
                    logger.info(f"已移除空临时目录: {self.temp_dir}")
                else:
                    logger.info(f"临时目录 {self.temp_dir} 非空，未移除。")
            except OSError as e:
                logger.warning(f"移除临时目录 {self.temp_dir} 失败: {e}")
        else:
            logger.info("插件临时目录未找到/定义/非预期，无需移除。")
        logger.info("GeminiArtist: terminate 清理完毕。")