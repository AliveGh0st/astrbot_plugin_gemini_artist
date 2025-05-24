from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.api.all import *
from astrbot.api.message_components import *
import asyncio
import sys
import importlib
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


@register("gemini_artist_plugin", "nichinichisou0609", "基于 Google Gemini 多模态模型的AI绘画插件", "1.0.0")
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

        # logger.info(f"GeminiExpPlugin __init__: Loaded robot_self_id: '{self.robot_id_from_config}'")
        # logger.info(f"GeminiExpPlugin __init__: Loaded group_whitelist: {self.group_whitelist}")
        # logger.info(f"GeminiExpPlugin __init__: Loaded random_api_key_selection: {self.random_api_key_selection}")

        shared_data_path = "/AstrBot/data" 
        self.plugin_temp_base_dir = os.path.join(shared_data_path, "gemini_artist_temp")
        os.makedirs(self.plugin_temp_base_dir, exist_ok=True)
        self.temp_dir = self.plugin_temp_base_dir
        # logger.critical(f"CRITICAL_INIT_LOG: self.temp_dir in __init__ is DEFINITIVELY SET TO: {self.temp_dir}")
        
        self.api_keys = [
            key.strip() 
            for key in api_key_list_from_config 
            if isinstance(key, str) and key.strip()
        ]
        self.current_api_key_index = 0
        
        # 存储正在等待输入的用户，键为 (user_id, session_id)
        self.waiting_users = {}  # {(user_id, session_id): expiry_time}
        # 存储用户收集到的文本和图片，键为 (user_id, session_id)
        self.user_inputs = {} # {(user_id, session_id): {'messages': [{'text': '', 'images': [], 'timestamp': float}]}}
        
        if not self._check_packages():
            self._install_packages()
        
        if not self.api_keys:
            logger.warning("Gemini API密钥未配置或配置为空。插件可能无法正常工作。")

        
    def _check_packages(self) -> bool:
        """检查是否安装了需要的包"""
        try:
            importlib.import_module('google.generativeai')
            importlib.import_module('PIL')
            return True
        except ImportError:
            return False

    def _install_packages(self):
        """安装必要的包"""
        try:
            import subprocess
            subprocess.check_call([sys.executable, "-m", "pip", "install", "-U", "genai", "pillow"])
            logger.info("成功安装必要的包: genai, pillow")
        except subprocess.CalledProcessError as e:
            logger.error(f"安装包失败: {str(e)}")
            raise
        
    @filter.command("draw")
    async def initiate_creation_session(self, event: AstrMessageEvent):
        """处理 /draw 命令，启动绘图会话。"""
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
        user_name = event.get_sender_name()
        
        session_id = user_id # 默认私聊时 session_id 就是 user_id
        is_group_message = event.message_obj.type.name == 'GROUP_MESSAGE'

        if is_group_message:
             if hasattr(event.message_obj, 'group_id') and event.message_obj.group_id:
                session_id = event.message_obj.group_id
             else:
                 logger.error(f"gemini_draw: 群聊消息但未找到群组ID: {event.message_obj.raw_message}")
                 yield event.plain_result("检测到群聊消息但未找到群组ID，无法启动绘制会话。")
                 return
        
        # 白名单检查
        if self.group_whitelist: # 仅当白名单列表有内容时才进行检查
            # 对于群聊，检查 group_id 是否在白名单中
            # 对于私聊，检查 user_id 是否在白名单中
            identifier_to_check = session_id if is_group_message else user_id
            # 将白名单中的ID转换为字符串进行比较，以防配置中是数字
            if str(identifier_to_check) not in [str(whitelisted_id) for whitelisted_id in self.group_whitelist]:
                logger.info(f"gemini_draw: 用户/群组 {identifier_to_check} 不在白名单中，已忽略。")
                # 可以选择不回复，或者回复一个提示信息
                # yield event.plain_result("抱歉，您没有权限使用此功能。") 
                return

        session_key = (user_id, session_id)

        if session_key in self.waiting_users:
             yield event.plain_result(f"您已经在当前会话有一个正在进行的绘制任务，请先完成或等待超时 ({int(self.waiting_users[session_key] - time.time())}秒后)。")
             return

        self.waiting_users[session_key] = time.time() + 30
        self.user_inputs[session_key] = {'messages': []}
        
        # logger.debug(f"Gemini_Draw: User {user_id} started draw. Message Type: {event.message_obj.type}, Session ID: {session_id}, Session Key: {session_key}. Waiting state set.")
        yield event.plain_result(f"好的 {user_name}，请在30秒内发送文本描述和可能需要的图片, 然后发送包含'start'或'开始'的消息开始生成。")
    
    @filter.event_message_type(EventMessageType.ALL)
    async def collect_user_inputs(self, event: AstrMessageEvent):
        """处理后续消息，收集用户输入或触发生成。"""
        if not hasattr(event, 'message_obj') or not hasattr(event.message_obj, 'type'):
             # logger.error(f"collect_user_inputs: 事件对象缺少 message_obj 或 type 属性: {type(event)}")
             return 

        user_id = event.get_sender_id()
        current_session_id = user_id 
        is_group_message = event.message_obj.type.name == 'GROUP_MESSAGE'

        if is_group_message:
            if hasattr(event.message_obj, 'group_id') and event.message_obj.group_id:
                 current_session_id = event.message_obj.group_id
            # else:
                 # logger.error(f"collect_user_inputs: 群聊消息但未找到群组ID: {event.message_obj.raw_message}")
                 # pass # 即使没有 group_id，也继续使用 user_id 作为 session_id

        # 白名单检查 (同样应用于后续消息，确保只有授权会话可以继续)
        if self.group_whitelist:
            identifier_to_check = current_session_id if is_group_message else user_id
            if str(identifier_to_check) not in [str(whitelisted_id) for whitelisted_id in self.group_whitelist]:
                # logger.info(f"collect_user_inputs: 用户/群组 {identifier_to_check} 不在白名单中，已忽略后续消息。")
                return

        current_session_key = (user_id, current_session_id)

        if not isinstance(event, AstrMessageEvent):
            logger.error(f"collect_user_inputs: 收到了错误类型的参数: {type(event)}")
            return

        # logger.debug(f"collect_user_inputs: Processing message. User ID: {user_id}, Message Type: {event.message_obj.type}, Session ID: {current_session_id}, Session Key: {current_session_key}")
        # logger.debug(f"collect_user_inputs: Current waiting users keys: {list(self.waiting_users.keys())}")

        if self.robot_id_from_config and user_id == self.robot_id_from_config:
            # logger.debug(f"collect_user_inputs: 消息来自机器人自身 ({user_id})，已忽略。")
            return

        message_text_raw = event.message_str.strip()
        keywords = ["start", "开始"]
        contains_keyword = any(keyword in event.message_str.lower() for keyword in keywords)
        is_command = message_text_raw.startswith("/") or message_text_raw.lower().startswith("draw")

        if is_command and not contains_keyword:
            # logger.debug(f"collect_user_inputs: 消息是命令 ({message_text_raw}) 且不包含 start/开始，已忽略。")
            return

        if current_session_key not in self.waiting_users:
            # logger.debug(f"collect_user_inputs: Session key {current_session_key} not found in waiting users. Ignoring message.")
            return

        # logger.debug(f"collect_user_inputs: Session key {current_session_key} IS found in waiting users. Proceeding.")

        if time.time() > self.waiting_users[current_session_key]:
            # logger.debug(f"collect_user_inputs: Session {current_session_id} for user {user_id} timed out.")
            del self.waiting_users[current_session_key]
            if current_session_key in self.user_inputs:
                del self.user_inputs[current_session_key]
            yield event.plain_result("等待超时，请重新发送命令。")
            return

        message_chain = event.get_messages()
        current_text = event.message_str
        current_images = []

        for msg in message_chain:
            if isinstance(msg, Image):
                try:
                    if hasattr(msg, 'url') and msg.url:
                        temp_img_path = await download_image_by_url(msg.url)
                        img = PILImage.open(temp_img_path)
                        img = img.convert("RGBA")
                        current_images.append(img)
                        # logger.info(f"Successfully downloaded image: {msg.url}")
                except Exception as e:
                    logger.error(f"collect_user_inputs: 处理图片失败: {str(e)}")
                    yield event.plain_result(f"无法处理图片，请稍后再试或尝试其他图片。错误: {str(e)}")
                    return

        if current_session_key not in self.user_inputs:
             logger.error(f"collect_user_inputs: 用户 {user_id} 在会话 {current_session_id} 中等待，但 user_inputs 状态丢失。正在清理。")
             if current_session_key in self.waiting_users:
                 del self.waiting_users[current_session_key]
             yield event.plain_result("您的等待状态异常，请重试。")
             return

        message_data = {
          'text': current_text,
          'images': current_images,
          'timestamp': time.time()
         }
        self.user_inputs[current_session_key]['messages'].append(message_data)

        if contains_keyword:
            # logger.debug(f"Gemini_Draw: Start keyword detected in session {current_session_id} for user {user_id}. Processing messages.")
            collected_messages = sorted(self.user_inputs[current_session_key]['messages'], key=lambda x: x['timestamp'])
            all_text = '\n'.join([msg['text'] for msg in collected_messages])
            all_images = [img for msg in collected_messages for img in msg['images']]

            for keyword in keywords:
                 all_text = re.sub(r'\b' + re.escape(keyword) + r'\b', '', all_text, flags=re.IGNORECASE).strip()

            del self.waiting_users[current_session_key]
            del self.user_inputs[current_session_key]

            if not all_text and not all_images:
                yield event.plain_result("请提供文本描述或图片。")
                return

            yield event.plain_result("正在处理您的请求，请稍候...")
            
            try:
                # logger.debug("collect_user_inputs: Calling gemini_generate...")
                result = await self.gemini_generate(all_text, all_images)
                # logger.debug(f"collect_user_inputs: gemini_generate call completed.")

                if result is None:
                    logger.error("collect_user_inputs: gemini_generate 返回 None!")
                    yield event.plain_result("处理图片时发生严重内部错误（无法获取处理结果）。")
                    return
                if not isinstance(result, dict):
                    logger.error(f"collect_user_inputs: gemini_generate 返回非字典类型: {type(result)}")
                    yield event.plain_result("处理图片时发生严重内部错误（结果格式错误）。")
                    return

                text_response = result.get('text', '').strip()
                image_paths = result.get('image_paths', []) 
                # logger.debug(f"FUNC gemini_generate RETURNED: text_response_preview='{text_response[:100]}...', image_paths_count={len(image_paths)}, image_paths_list={image_paths}")

                if not text_response and not image_paths:
                    logger.warning("collect_user_inputs: API未返回任何文本或图片内容。")
                    yield event.plain_result("未能从API获取任何文本或图片内容。")
                    return

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
                    logger.error("collect_user_inputs: 无法确定机器人UIN。降级处理。")
                    if text_response: yield event.plain_result(text_response)
                    for img_path in image_paths:
                        if img_path and os.path.exists(img_path) and os.path.getsize(img_path) > 0: yield event.chain_result([Image.fromFileSystem(img_path)])
                        else: logger.error(f"collect_user_inputs: 降级发送时图片无效: {img_path}")
                    return 
                # logger.info(f"BRANCH_NODES_MSG_UIN_OK: UIN={bot_id_for_node} (来自 {bot_uin_source}), Name='{bot_name_for_node}'.")
                
                ns = Nodes([])
                if text_response:
                    try:
                        text_node_content = [Plain(text_response)]
                        ns.nodes.append(Node(uin=bot_id_for_node, name=bot_name_for_node, content=text_node_content))
                        # logger.debug(f"BRANCH_NODES_MSG_TEXT_NODE_ADDED: 文本Node已添加。Nodes count: {len(ns.nodes)}. Content: {text_response[:50]}")
                    except Exception as e_text_node:
                        logger.error(f"collect_user_inputs: 创建文本Node失败: {e_text_node}", exc_info=True)

                valid_image_node_count = 0
                for i, img_path in enumerate(image_paths):
                    # logger.debug(f"BRANCH_NODES_MSG_IMG_NODE_ATTEMPT: 尝试图片 {i+1}/{len(image_paths)}, path='{img_path}'")
                    if img_path and os.path.exists(img_path) and os.path.getsize(img_path) > 0:
                        try:
                            img_node_chain = [Plain(f"图片 {i+1}/{len(image_paths)}"), Image.fromFileSystem(img_path)]
                            ns.nodes.append(Node(uin=bot_id_for_node, name=bot_name_for_node, content=img_node_chain))
                            valid_image_node_count += 1
                            # logger.debug(f"BRANCH_NODES_MSG_IMG_NODE_ADDED: 图片Node {img_path} 已添加。Nodes count: {len(ns.nodes)}")
                        except Exception as e_img_node:
                            logger.error(f"collect_user_inputs: 创建图片Node {img_path} 失败: {e_img_node}", exc_info=True)
                    # else:
                        # logger.error(f"BRANCH_NODES_MSG_IMG_NODE_INVALID_PATH: 图片无效: path='{img_path}', exists={os.path.exists(img_path) if img_path else 'N/A'}, size={os.path.getsize(img_path) if img_path and os.path.exists(img_path) else 'N/A'}")
                
                # logger.info(f"BRANCH_NODES_MSG_BUILD_COMPLETE: Nodes构建完成。总计Nodes: {len(ns.nodes)} (其中有效图片Nodes: {valid_image_node_count}).")

                if ns.nodes:
                    # logger.info(f"BRANCH_NODES_MSG_YIELDING: 准备发送 {len(ns.nodes)} 个Nodes。")
                    yield event.chain_result([ns])
                    # logger.info(f"BRANCH_NODES_MSG_YIELDED: {len(ns.nodes)} 个Nodes已yield。")
                else:
                    logger.error("collect_user_inputs: ns.nodes列表为空，无法发送合并转发。")
                    yield event.plain_result("抱歉，API未能生成可显示的有效内容。")
                return 

            except Exception as e_main_handler:
                logger.error(f"collect_user_inputs: 处理API响应或构建回复时发生顶层错误: {str(e_main_handler)}", exc_info=True)
                yield event.plain_result(f"处理请求时发生严重内部错误，请联系管理员。")
                return 
        
        else: 
            if current_text.strip() or current_images: 
                # logger.debug(f"CONTINUE_COLLECTING: 未检测到开始指令，收到输入: text='{current_text[:50]}...', images_count={len(current_images)}")
                yield event.plain_result("已收到您的输入，请继续发送或发送包含'start'或'开始'的消息结束。")
            # else:
                # logger.debug("CONTINUE_COLLECTING: 收到空消息或仅含空格的消息，且不含开始指令，已忽略。")

    async def gemini_generate(self, text, images):
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
                if text:
                    contents.append(text)
                for img in images:
                    contents.append(img)

                if len(contents) == 2 and text and len(images) == 1:
                    contents = (text, images[0])

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
        self.waiting_users.clear()
        self.user_inputs.clear() 
        # 清理临时文件
        if hasattr(self, 'temp_dir') and os.path.exists(self.temp_dir):
                if self.temp_dir == self.plugin_temp_base_dir: # 确保是插件自己的目录
                    try:
                        os.rmdir(self.temp_dir) # 仅当目录为空时才能成功
                        logger.info(f"terminate: 已移除临时目录: {self.temp_dir}")
                    except OSError as e_rmdir:
                        logger.warning(f"terminate: 移除临时目录 {self.temp_dir} 失败 (可能不为空): {e_rmdir}")
        else:
            logger.info("terminate: 临时目录未找到或未定义，无需清理。")
