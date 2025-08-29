# Gemini Artist 插件 - 便捷的Gemini/OpenRouter生图模型控制助手,用于Astrbot平台

本插件能够让您便捷使用 Google 的 `gemini-2.0-flash-exp`、`gemini-2.0-flash-exp-image-generation`、`gemini-2.0-flash-preview-image-generation` 等模型进行图像生成；同时也支持 OpenRouter（OpenAI API 规范）格式的图像生成模型，例如 `google/gemini-2.5-flash-image-preview`（OpenRouter 每账号每日可免费使用约 50 次，该模型为谷歌较新的多模态图像输出模型，生成质量优于前代）。

> 由于框架更新，v3.5.17能够正常使用；v3.5.18函数调用无法发送任何信息；v3.5.19能够收到消息与图片，但不能正确处理返回给llm的工具调用结果，而是直接发送给用户。  
> 注意：本插件仅在 aicoqhttp 协议下的 QQ 平台进行过测试，其他协议平台可用性未知，欢迎反馈。

## 🚀 使用方法

- 启用插件，并与能够使用函数工具的平台大模型对话，要求其生成/画/图像处理等即可实现自动调用，可通过对话或引用指定参考图片，支持多张图片作为参考。
- 如果大模型没有调用该工具，请向大模型明确您的需求再做尝试。
- 使用 `/draw` 指令同样可以调用该工具。
- 现在支持“再画一张”、“重新生成一张”等自然语言提示，模型会自动从上下文中复用上一次绘画的 prompt。

## 🎨 使用示例

 ![alt text](img/img3.jpg)  ![alt text](img/img4.jpg)  
 ![alt text](img/img2.jpg)

## 🔑 配置指南

1.  获取 API Key：
    - Google：前往 [Google AI Studio](https://aistudio.google.com/apikey) 免费获取。
    - OpenRouter：前往 [OpenRouter](https://openrouter.ai) 获取，并在“Settings”中创建 API Key。

2.  插件配置：AstrBot 平台为插件提供了便捷的图形化配置界面。请在 AstrBot 的插件管理后台找到 `Gemini Artist`（注册名 `gemini_artist_plugin`），点击配置，然后根据界面提示填入以下信息：

    - `api_type`：API 类型。可选：
      - `Google`（默认）
      - `OpenRouter`
    - `api_key`：API Key 列表（可以是一个或多个）。支持 Google Gemini Key 或 OpenRouter Key。
    - `api_base_url`：（可选）API 的基础 URL。
      - 使用 Google 官方时：`https://generativelanguage.googleapis.com`（默认）
      - 使用 OpenRouter 时：推荐填写 `https://openrouter.ai` 或 `https://openrouter.ai/`
        - 插件会自动补全为兼容的 `/api/v1` 路径。
      - 如果你使用自建或其它兼容 OpenAI Chat Completions 的服务，请填写其 base url。
    - `model`：（可选）进行生图的模型。默认为 `gemini-2.0-flash-exp`。该字段现在为自定义字符串，便于你手动填入任意可用模型名。
      - Google 官方示例：`gemini-2.0-flash-exp`、`gemini-2.0-flash-exp-image-generation`、`gemini-2.0-flash-preview-image-generation`
      - OpenRouter 示例：`google/gemini-2.5-flash-image-preview`
    - `max_cached_images`：（可选）缓存的用户图片 URL 最大数量。默认为 `5`。仅在需要作为参考时下载，否则只缓存图片地址。
    - `robot_self_id`：（可选）机器人自身的 ID，用于忽略机器人自身发送的消息。
    - `group_whitelist`：（可选）群聊白名单。一个包含群组 ID 或用户 ID 的列表。为空则对所有会话生效；不为空则仅对列表中的群组或用户私聊生效。
    - `random_api_key_selection`：（可选）布尔值，默认为 `false`（顺序轮询 API Key）。设为 `true` 时，将从 `api_key` 列表中随机选择一个 Key 进行调用。
    - `temp_cleanup_interval_seconds`：（可选）后台定时清理临时目录的间隔时间（秒）。`0` 表示禁用定时清理。默认为 `21600`（6 小时）。
    - `temp_cleanup_files_older_than_seconds`：（可选）清理时，将清理临时目录中存放超过此时间（秒）的文件。默认为 `259200`（3 天）。
    - `enable_base_reference_image`：（可选）布尔值，默认为 `false`。启用后，在没有提供任何其他参考图时，将使用下面配置的默认图片作为生图参考。
    - `base_reference_image_path`：（可选）字符串。默认参考图片的本地路径。请使用绝对路径或相对于 AstrBot 根目录的路径（例如：`data/my_style.png`）。
    - `enable_hinting`：（可选）布尔值。开启后，在生成过程会发送“正在生成图片，请稍候...”提示（v1.4.1+ 可配置关闭该提示）。

3.  网络代理（如果需要）：
    - 如果您无法直接访问 Google API（`https://generativelanguage.googleapis.com`），请确保您的 AstrBot 配置了正确的网络代理，或者通过 `api_base_url` 配置项将 API 地址替换为您的反代地址。
    - 使用 OpenRouter 时，无需能直接访问 Google；确保能访问 `https://openrouter.ai` 即可。

## 🤔 为何选择本插件？

- 免费层级 Gemini API 的 `gemini-2.0-flash-exp-image-generation` 等模型的 RPM/RPD/TPM 都较低，单独作为插件使用可以避免将限额消耗在非图像生成功能上，专注生图。
- 这类模型通常不支持系统提示词/函数调用且上下文长度较短，并不适合作为日常助手使用。
- 插件实现了会话隔离，避免多个用户同时使用或单个用户在不同会话中同时使用导致的相互干扰。
- 提供便捷的 API Base URL 设置，方便使用反向代理，或切换到 OpenRouter、OpenAI 兼容服务。
- 更灵活的 API Key 和模型管理：支持顺序轮询与随机选择 API Key，支持自定义填写模型名，避免因单个 Key 限制导致失败或长期只使用一个 Key。
- 支持常见带透明度的 “P” 模式 QQ 表情包。
- 功能以平台大模型“函数工具”方式注册，使用自然、便捷。
- 新增对 OpenRouter 图像生成模型的支持：如 `google/gemini-2.5-flash-image-preview`，OpenRouter 每账号每日约 50 次免费额度，质量更优。
- 优化提示词并加入英文通用前缀，有助于提升调用绘图成功率；支持“再画一张/重新生成一张”等再生成场景，自动继承最近一次绘画的提示词。

## 🤝 支持与贡献

- 如果该项目对您有帮助，请 star⭐！
- 问题反馈和建议，请通过 AstrBot 官方渠道或插件仓库提交 Issue。
- 欢迎对代码进行改进和贡献！

[AstrBot 官方文档](https://astrbot.app)

## 更新日志

### v1.5.0
- 增加对 OpenRouter（OpenAI Chat Completions 格式）图像生成模型的支持，推荐 `google/gemini-2.5-flash-image-preview`（OpenRouter 每账号每日约 50 次免费调用）。
- 优化提示词，在绘图前自动添加英文前缀以提升成功率；支持“再画一张/重新生成一张”自然语言再生成，自动复用上下文中的 prompt。
- 模型配置项改为手动填写字符串，便于用户自定义更多模型。
- OpenRouter 的 API 也兼容其他使用 OpenAI Chat Completions 接口进行图像生成的服务。

#### v1.4.1
-   [一些bug修复和改进](https://github.com/nichinichisou0609/astrbot_plugin_gemini_artist/pull/11)
- 增加了关闭生图中提示的开关（`enable_hinting`）。
- 存在某些版本下无法让 LLM 正确获取工具调用返回信息的问题。

### v1.4.0
- 新增“默认参考图”功能：可在插件配置中指定一张默认图片，当用户未提供任何参考图时，将自动使用该图片进行创作，方便统一风格。

##### v1.3.3
- 恢复了旧版本的指令调用方式（此方式的生成内容不会进入上下文，但仍然会加入 bot 生成图片的缓存中）。

##### v1.3.2
- 修复了私聊存储错误 key 图片 url 的问题。
- 修复了不能获取全部的引用消息图片的问题。

##### v1.3.1
- 修复了生成多张图片时，无法正确输出合并消息的问题。
- 修复了错误的使用绝对路径的问题 @[Issue #8](https://github.com/nichinichisou0609/astrbot_plugin_gemini_artist/issues/8)，感谢 @xu-wish 的反馈。

### v1.3.0
- 修复了函数调用无法参考多张图片的问题。
- 现在可以通过对话让 LLM 参考自己生成的图片了。
- 现在 LLM 描述生成的图片时，会使用当前对话人格回复了。

### v1.2.0
- 放弃了使用指令的方式，改为使用函数工具的方式，使用更加便捷。
- 默认使用中文回复。

##### v1.1.2
- 去掉了不必要的初始化检查。

##### v1.1.1
- 修复了安装了错误依赖的问题（造成了 AstrBot 的意外崩溃）。

### v1.1.0
- 增加了对临时文件的定时清理功能，默认 6 小时清理一次，可在插件配置中进行修改。
- 现在可自定义机器人等待的时间了，默认为 30 秒。