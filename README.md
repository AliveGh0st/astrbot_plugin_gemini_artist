# Gemini Artist 插件 - 便捷的Gemini生图模型控制助手,用于Astrbot平台

本插件能够让您便捷使用 Google的 `gemini-2.0-flash-exp` 、`gemini-2.0-flash-exp-image-generation`、`gemini-2.0-flash-preview-image-generation`模型进行图像生成(本人已知的Gemini免费api能够使用的三种生图模型，性能区别未知)。

>注意：本插件仅在aicoqhttp协议下的QQ平台进行过测试，其他协议平台可用性未知，欢迎反馈。

## 🤔 为何选择本插件？

您可能会问，为什么不直接将 AstrBot 中的LLM提供商设置为 `gemini-2.0-flash-exp-image-generation` 等模型来进行图像生成，而是使用一个插件进行单独调用呢？主要因素：

*   免费层级Gemini-API的`gemini-2.0-flash-exp-image-generation等模型`每分钟请求数（RPM）、每日请求数（RPD）、每分钟令牌数（TPM）都很少，单独使用可以避免将限额消耗在非图像生成功能上，专注生图。
*   gemini-2.0-flash-exp-image-generation等模型`不支持系统调用词、不支持函数调用且上下文长度仅在32K左右的水平`，并不适合作为日常助手使用
*   本插件实现了会话隔离，避免因多个用户同时使用或单个用户在不同会话中同时使用而导致的问题。
*   本插件提供便捷的反代URL设置，方便您使用反向代理。
*   本插件提供了更灵活的API Key、模型管理功能，支持顺序轮询和随机选择API Key和切换模型，避免因单个API Key的限制导致的生成失败或长期只使用一个API Key。
*   支持常见的带有透明度的 "P" 模式QQ表情包。


## 🔑 配置指南

1.  **获取API Key**：您需要一个有效的 Google Gemini API Key。可以前往 [Google AI Studio](https://aistudio.google.com/apikey) 免费获取。
2.  **插件配置**：AstrBot 平台为插件提供了便捷的图形化配置界面。请在 AstrBot 的插件管理后台找到 `Gemini Artist` (或其注册名 `gemini_artist_plugin`)，点击配置，然后根据界面提示填入以下信息：
    *   `api_key`: 您的 Google Gemini API Key 列表 (可以是一个或多个)。
    *   `api_base_url`: (可选) Gemini API 的基础 URL。默认为官方地址 `https://generativelanguage.googleapis.com`。如果您使用了反向代理，请在此处填写您的代理地址(没测试过)。
    *   `robot_self_id`: (可选?) 机器人自身的ID，用于忽略机器人自身发送的消息。
    *   `group_whitelist`: (可选) 群聊白名单。一个包含群组ID或用户ID的列表。如果此列表为空，则插件对所有会话生效；如果列表不为空，则插件仅对列表中的群组或用户私聊生效。
    *   `random_api_key_selection`: (可选)布尔值，默认为 `false` (顺序轮询API Key)。设置为 `true` 时，将从 `api_key` 列表中随机选择一个Key进行调用。
3.  **网络代理** (如果需要)：如果您无法直接访问 Google API (`https://generativelanguage.googleapis.com`)，请确保您的Astrbot配置了正确的网络代理，或者通过 `api_base_url` 配置项将API地址替换为您的反代地址。

## 🚀 使用方法

核心命令是 `/draw`。

**方式一：一次性发送指令**

直接在 `/draw` 命令后跟上您的文本描述和图片（如果需要）。请注意在/draw命令后加上空格且紧接着的不能是图片。


**方式二：分步发送指令**

1.  发送 `/draw` 命令给机器人。
    ```
    /draw
    ```
2.  机器人会回复提示您在30秒内发送描述和图片。
3.  在30秒内，您可以发送一条或多条消息，包含：
    *   文本描述 (例如：`一只可爱的柯基犬`)
    *   图片 (直接发送图片即可)
4.  当您准备好所有素材后，发送一条包含关键词 `start` 或 `开始` 的消息来触发生成。
    ```
    开始创作吧！
    ```
    或者
    ```
    start
    ```

**示例会话 (分步)：**

> **您**： /draw  
> **机器人**：好的，请在30秒内发送文本描述和可能需要的图片, 然后发送包含'start'或'开始'的消息开始生成。 
> **您**：(发送一张风景图片)  
> **您**：帮我把这张图变成梵高风格  
> **您**：start  
> **机器人**：正在处理您的请求，请稍候...  
> (稍后机器人会发送生成的图片)  


## 🛠️ 首次运行

插件在首次加载时，如果检测到缺少必要的 Python 包 (`genai`, `pillow`)，会自动尝试安装。

## 🤝 支持与贡献

*   问题反馈和建议，请通过 AstrBot 官方渠道或插件仓库提交 Issue。
*   欢迎对代码进行改进和贡献！


[AstrBot 官方文档](https://astrbot.app)

## 更新日志

### v1.1.0

-   增加了对临时文件的定时清理功能，默认6小时清理一次，可在插件配置中进行修改。
-   现在可自定义机器人等待的时间了，默认为30秒。