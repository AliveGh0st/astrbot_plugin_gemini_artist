# Gemini Artist 插件 - 便捷的Gemini生图模型控制助手,用于Astrbot平台

本插件能够让您便捷使用 Google的 `gemini-2.0-flash-exp` 、`gemini-2.0-flash-exp-image-generation`、`gemini-2.0-flash-preview-image-generation`等模型进行图像生成(本人已知的Gemini免费api能够使用的三种生图模型，性能区别未知)。

>注意：本插件仅在aicoqhttp协议下的QQ平台进行过测试，其他协议平台可用性未知，欢迎反馈。

## 🚀 使用方法

*   启用插件，并与能够使用函数工具的平台大模型对话，要求其生成/画/图像处理等即可实现自动调用，可通过对话或引用指定参考图片，支持多张图片参考

## 🎨 使用示例

 ![alt text](img/img1.jpg) ![alt text](img/img3.jpg) ![alt text](img/img2.jpg)


## 🔑 配置指南

1.  **获取API Key**：您需要一个有效的 Google Gemini API Key。可以前往 [Google AI Studio](https://aistudio.google.com/apikey) 免费获取。
2.  **插件配置**：AstrBot 平台为插件提供了便捷的图形化配置界面。请在 AstrBot 的插件管理后台找到 `Gemini Artist` (或其注册名 `gemini_artist_plugin`)，点击配置，然后根据界面提示填入以下信息：
    *   `api_key`: 您的 Google Gemini API Key 列表 (可以是一个或多个)。
    *   `api_base_url`: (可选) Gemini API 的基础 URL。默认为官方地址 `https://generativelanguage.googleapis.com`。如果您使用了反向代理，请在此处填写您的代理地址(没测试过)。
    *   `model`: (可选) 进行生图的模型。默认为 `gemini-2.0-flash-exp`。可选模型包括 `gemini-2.0-flash-exp`、`gemini-2.0-flash-exp-image-generation`、`gemini-2.    0-flash-preview-image-generation`。
    *   `max_cached_images`: (可选) 缓存的用户图片URL最大数量。默认为 `5`。仅在参照时下载，否则只缓存图片地址。
    *   `robot_self_id`: (可选?) 机器人自身的ID，用于忽略机器人自身发送的消息。
    *   `group_whitelist`: (可选) 群聊白名单。一个包含群组ID或用户ID的列表。如果此列表为空，则插件对所有会话生效；如果列表不为空，则插件仅对列表中的群组或用户私聊生效。
    *   `random_api_key_selection`: (可选)布尔值，默认为 `false` (顺序轮询API Key)。设置为 `true` 时，将从 `api_key` 列表中随机选择一个Key进行调用。
    *   `temp_cleanup_interval_seconds`: (可选) 后台定时清理临时目录的间隔时间（秒）。`0` 表示禁用定时清理。默认为 `21600` (6小时)。
    *   `temp_cleanup_files_older_than_seconds`: (可选) 清理时，将清理临时目录中存放超过此时间（秒）的文件。默认为 `259200` (3天)。
3.  **网络代理** (如果需要)：如果您无法直接访问 Google API (`https://generativelanguage.googleapis.com`)，请确保您的Astrbot配置了正确的网络代理，或者通过 `api_base_url` 配置项将API地址替换为您的反代地址。



## 🤔 为何选择本插件？

您可能会问，为什么不直接将 AstrBot 中的LLM提供商设置为 `gemini-2.0-flash-exp-image-generation` 等模型来进行图像生成，而是使用一个插件进行单独调用呢？主要因素：

*   免费层级Gemini-API的`gemini-2.0-flash-exp-image-generation等模型`每分钟请求数（RPM）、每日请求数（RPD）、每分钟令牌数（TPM）都很少，单独使用可以避免将限额消耗在非图像生成功能上，专注生图。
*   gemini-2.0-flash-exp-image-generation等模型`不支持系统调用词、不支持函数调用且上下文长度仅在32K左右的水平`，并不适合作为日常助手使用
*   ~~本插件实现了会话隔离，避免因多个用户同时使用或单个用户在不同会话中同时使用而导致的问题。~~
*   本插件提供便捷的反代URL设置，方便您使用反向代理。
*   本插件提供了更灵活的API Key、模型管理功能，支持顺序轮询和随机选择API Key和切换模型，避免因单个API Key的限制导致的生成失败或长期只使用一个API Key。
*   支持常见的带有透明度的 "P" 模式QQ表情包。
*   本插件将功能注册为平台大模型函数工具（新）

## 🤝 支持与贡献

*   问题反馈和建议，请通过 AstrBot 官方渠道或插件仓库提交 Issue。
*   欢迎对代码进行改进和贡献！
*   如仍需使用指令调用请使用旧版

[AstrBot 官方文档](https://astrbot.app)

## 更新日志

### v1.3.0
-   修复了函数调用无法参考多张图片的问题
-   现在可以通过对话让llm参考自己生成的图片了
-   现在llm描述生成的图片时，会使用当前对话人格回复了

### v1.2.0
-   放弃了使用指令的方式，改为使用函数工具的方式，使用更加便捷。
-   默认使用中文回复

#### v1.1.2
-   去掉了不必要的初始化检查

#### v1.1.1
-   修复了安装了错误依赖的问题（造成了Astrbot的意外崩溃）

### v1.1.0

-   增加了对临时文件的定时清理功能，默认6小时清理一次，可在插件配置中进行修改。
-   ~~现在可自定义机器人等待的时间了，默认为30秒。~~v1.1.2 取消了这个功能。


