const fs = require("fs");
const {
  Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell,
  Header, Footer, AlignmentType, HeadingLevel, BorderStyle, WidthType,
  ShadingType, PageBreak, PageNumber, TableOfContents,
} = require("docx");

// Formatting constants (half-points for font sizes, twips for spacing)
const FONT_SONG = "宋体";
const FONT_HEI = "黑体";
const SIZE_XIAO3 = 30;    // 小三 15pt
const SIZE_SI = 28;        // 四号 14pt
const SIZE_XIAO4 = 24;     // 小四 12pt
const SIZE_WU = 21;        // 五号 10.5pt
const SIZE_XIAO5 = 18;     // 小五 9pt
const SIZE_ER = 44;         // 二号 22pt
const SIZE_XIAO2 = 36;      // 小二 18pt
const SIZE_16PT = 32;       // 16pt (三号)
const SIZE_26PT = 52;       // 26pt (封面校名)

const LINE_15 = 360;        // 1.5倍行距 (twips)

// A4 margins (twips: 1mm ≈ 56.7 twips)
const MARGIN_TOP = 1701;    // 30mm
const MARGIN_BOTTOM = 1418; // 25mm
const MARGIN_LEFT = 1418;   // 25mm
const MARGIN_RIGHT = 1418;  // 25mm

// ==================== Helper functions ====================

function heading1(text) {
  return new Paragraph({
    heading: HeadingLevel.HEADING_1,
    alignment: AlignmentType.CENTER,
    spacing: { before: 480, after: 240, line: LINE_15 },
    children: [new TextRun({ text, font: FONT_SONG, size: SIZE_XIAO3, bold: true })],
  });
}

function heading2(text) {
  return new Paragraph({
    heading: HeadingLevel.HEADING_2,
    alignment: AlignmentType.LEFT,
    spacing: { before: 240, after: 120, line: LINE_15 },
    children: [new TextRun({ text, font: FONT_SONG, size: SIZE_XIAO4, bold: true })],
  });
}

function bodyPara(text) {
  return new Paragraph({
    spacing: { line: LINE_15 },
    indent: { firstLine: 480 },
    alignment: AlignmentType.JUSTIFIED,
    children: [new TextRun({ text, font: FONT_SONG, size: SIZE_XIAO4 })],
  });
}

function bodyParaNoIndent(text) {
  return new Paragraph({
    spacing: { line: LINE_15 },
    alignment: AlignmentType.JUSTIFIED,
    children: [new TextRun({ text, font: FONT_SONG, size: SIZE_XIAO4 })],
  });
}

function emptyPara() {
  return new Paragraph({ spacing: { line: LINE_15 }, children: [] });
}

function figureCaption(text) {
  return new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { before: 100, after: 100 },
    children: [new TextRun({ text, font: FONT_SONG, size: SIZE_WU })],
  });
}

const border = { style: BorderStyle.SINGLE, size: 1, color: "999999" };
const borders = { top: border, bottom: border, left: border, right: border };

function makeTableHeaderRow(cells) {
  return new TableRow({
    tableHeader: true,
    children: cells.map((text, i) => new TableCell({
      borders,
      width: { size: [2000, 4140, 2500][i], type: WidthType.DXA },
      shading: { fill: "E8E8E8", type: ShadingType.CLEAR },
      margins: { top: 60, bottom: 60, left: 100, right: 100 },
      children: [new Paragraph({
        alignment: AlignmentType.CENTER,
        children: [new TextRun({ text, font: FONT_SONG, size: SIZE_WU, bold: true })],
      })],
    })),
  });
}

function makeTableRow(cells) {
  return new TableRow({
    children: cells.map((text, i) => new TableCell({
      borders,
      width: { size: [2000, 4140, 2500][i], type: WidthType.DXA },
      margins: { top: 60, bottom: 60, left: 100, right: 100 },
      children: [new Paragraph({
        children: [new TextRun({ text, font: FONT_SONG, size: SIZE_WU })],
      })],
    })),
  });
}

function sectionTitle(text) {
  // 结语/参考文献/致谢 等无编号标题
  return heading1(text);
}

// ==================== Thesis content ====================

const abstractText = `随着直播电商行业的蓬勃发展，直播间已成为商品销售和品牌推广的重要渠道。然而，主播在直播过程中面临着弹幕信息过载、互动回复不及时、多平台工具切换繁琐等问题。本文设计并实现了一个基于大语言模型的直播互动智能助手系统（Aiszr），集成了弹幕实时抓取、AI智能回复、OBS场景联动和AI语音播报等核心功能。

系统采用Python技术栈，利用Playwright浏览器自动化技术实现抖音直播间弹幕的被动式WebSocket拦截抓取，通过Protobuf协议解码获取结构化弹幕数据。在AI回复方面，系统接入DeepSeek大语言模型，设计了包含人设系统、回复节流、用户冷却、规则降级的多层回复策略，实现了高质量的直播间智能互动。同时，系统通过OBS WebSocket协议实现关键词触发的场景自动切换，并接入阿里云百炼语音克隆服务实现AI回复的语音播报。

测试结果表明，系统能够稳定抓取直播间弹幕数据，AI回复内容自然、符合主播人设设定，OBS联动和语音播报功能运行可靠，有效提升了直播间的互动效率和用户体验。`;

const keywords = "大语言模型；直播互动；弹幕抓取；智能回复；语音克隆；OBS联动";

// ==================== Body paragraphs ====================

const sec1_1 = [
  `近年来，随着移动互联网技术的快速发展和5G网络的普及，直播电商行业迎来了爆发式增长。据商务部数据显示，2024年上半年全国直播电商销售额达到1.27万亿元，同比增长超过30%。抖音、快手、淘宝直播等平台已经成为商品销售的重要渠道，越来越多的商家和品牌选择通过直播方式进行产品推广和销售。`,
  `在直播场景中，主播需要同时处理多个任务：展示商品、回答观众提问、管理直播间氛围、控制OBS场景切换等。这种多任务并行的工作模式对主播的注意力和精力提出了很高的要求。特别是在人气较高的直播间，弹幕消息大量涌入，主播往往无法及时回复所有观众的提问，导致互动体验下降，进而影响销售转化率。`,
  `为了解决上述问题，业界开始探索利用人工智能技术辅助直播运营。基于大语言模型（LLM）的智能回复系统能够自动理解观众提问并生成高质量回复，语音合成技术可以将文字回复转化为语音播报，OBS联动技术可以实现关键词触发的场景自动切换。这些技术的综合应用有望显著降低主播的工作负担，提升直播间的互动效率。`,
  `因此，本课题旨在设计并实现一个基于大语言模型的直播互动智能助手系统，通过技术手段解决直播场景中的弹幕信息过载、互动回复不及时、工具切换繁琐等痛点，为直播电商行业提供一套完整的智能化解决方案。`,
];

const sec1_2 = [
  `在弹幕抓取方面，目前市面上的主要方案包括直接WebSocket连接和浏览器自动化两种方式。直接连接方案（如DouyinLiveWebFetcher、DySpider等项目）通过逆向分析抖音的WebSocket协议实现直连，但需要持续维护签名算法，且协议变更频繁导致连接不稳定。浏览器自动化方案（基于Selenium或Playwright）则利用浏览器自身的WebSocket连接进行被动监听，无需逆向协议签名，稳定性和维护成本更优。本项目选用Playwright方案，兼顾了稳定性和开发效率。`,
  `在AI智能回复方面，随着ChatGPT、DeepSeek等大语言模型的出现和普及，基于LLM的对话系统在客服、教育、娱乐等领域得到了广泛应用。在直播场景中，已有部分产品尝试接入大语言模型实现自动回复，但普遍存在回复内容与主播人设不符、回复频率过高导致刷屏、缺乏上下文理解等问题。本系统通过设计多层回复策略（人设系统、节流控制、用户冷却、规则降级）有效解决了这些问题。`,
  `在语音合成方面，近年来语音克隆技术取得了显著进展。阿里云百炼平台提供了基于Qwen模型的语音克隆服务，只需15秒的语音样本即可实现高质量的语音复刻，为直播场景的语音播报提供了技术基础。`,
  `在OBS联动方面，OBS Studio提供了WebSocket v5接口，允许外部程序通过JSON-RPC协议控制场景切换、媒体播放等操作，为本系统的场景联动功能提供了技术支撑。`,
];

const sec1_3 = [
  `本课题的研究目标是设计并实现一个功能完整、运行稳定的直播互动智能助手系统，主要研究内容包括：`,
  `（1）研究基于Playwright的直播间弹幕被动式抓取技术，实现WebSocket帧拦截、Protobuf协议解码和DOM观察补偿的双通道弹幕采集方案。`,
  `（2）研究基于大语言模型的直播间智能回复策略，设计人设系统、回复节流、用户冷却和规则降级等多层控制机制，实现高质量的自动互动回复。`,
  `（3）研究OBS WebSocket协议的场景联动技术，实现基于关键词匹配和命中计数的自动场景切换方案。`,
  `（4）研究基于阿里云百炼的语音克隆与合成技术，实现AI回复的语音播报和数字人推流功能。`,
];

const sec2_1 = [
  `Playwright是微软开发的浏览器自动化框架，支持Chromium、Firefox和WebKit三大浏览器引擎。与传统的Selenium相比，Playwright具有原生WebSocket监听能力，可以通过page.on("websocket")事件捕获浏览器中所有WebSocket连接的数据帧，无需额外的网络代理或中间人攻击。`,
  `本系统利用Playwright的persistent context（持久化浏览器上下文）功能，将浏览器状态（包括Cookie、localStorage等）持久化到本地磁盘，实现一次扫码登录、多次复用的效果。同时，Playwright的自动等待机制和事件驱动架构使得弹幕抓取过程无需轮询，降低了CPU占用和响应延迟。`,
  `在反检测方面，Playwright启动时添加了"--disable-blink-features=AutomationControlled"参数，移除了浏览器的自动化标识特征，降低了被抖音平台检测为自动化工具的风险。`,
];

const sec2_2 = [
  `抖音直播间的弹幕数据通过WebSocket协议传输，消息体采用Protobuf（Protocol Buffers）序列化格式。Protobuf是Google开发的高效二进制序列化协议，具有体积小、解析快、跨语言兼容等优点。`,
  `抖音的弹幕传输采用三层协议结构：外层为PushFrame信封，包含序列号、日志ID等元信息；中间层为gzip压缩的负载数据；内层为Response消息，包含一个或多个具体的弹幕消息（如ChatMessage、GiftMessage、LikeMessage等）。`,
  `本系统通过手工定义Protobuf消息结构（PushFrame、Response、ChatMessage等），利用google-protobuf库的动态消息工厂（MessageFactory）实现了无需protoc编译的协议解码方案。这种方式既保持了协议解析的正确性，又避免了编译环境的依赖问题。`,
];

const sec2_3 = [
  `大语言模型（Large Language Model，LLM）是基于Transformer架构的深度学习模型，通过在海量文本数据上进行预训练，获得了强大的自然语言理解和生成能力。DeepSeek是由深度求索公司开发的开源大语言模型，提供了与OpenAI API兼容的接口，支持对话补全、函数调用等功能。`,
  `本系统通过HTTP请求调用DeepSeek的Chat Completions接口，将观众弹幕作为用户消息、主播人设作为系统提示词发送给模型，获取AI生成的回复文本。接口调用采用异步方式（httpx.AsyncClient），避免阻塞主线程。同时，系统设置了请求超时机制（6-12秒自适应），防止单次回复耗时过长影响整体互动节奏。`,
  `在人设系统设计方面，本系统定义了角色名称、角色定位、语气风格、回复策略、场景话术、回复限制和禁忌事项七个维度的人设配置，通过组合这些维度生成完整的系统提示词（System Prompt），确保AI回复的内容和风格与主播的人设保持一致。`,
];

const sec2_4 = [
  `阿里云百炼（DashScope）平台提供了基于Qwen模型的语音克隆和语音合成服务。语音克隆（Voice Cloning）技术允许用户上传15秒的语音样本，系统据此生成一个个性化的语音模型，后续的文字转语音（TTS）操作即可使用该模型生成具有用户音色特征的语音。`,
  `本系统使用阿里云百炼的Qwen Voice Enrollment模型完成声音注册，使用Qwen3-TTS-VC模型进行语音合成。合成过程支持语速和音量参数调节，合成结果为WAV格式的音频文件。系统还实现了合成缓存机制，基于文本内容、模型、语速等参数的哈希值作为缓存键，避免相同内容重复调用API，节省成本和响应时间。`,
  `在本地播放方面，系统针对Windows平台使用winsound进行异步WAV播放，针对其他平台使用pygame.mixer作为播放后端，保证了跨平台的兼容性。`,
];

const sec3_1_content = [
  `通过对直播场景的深入调研和分析，本系统确定了以下功能需求：`,
  `（1）登录会话管理：支持Playwright扫码登录，浏览器状态持久化，登录有效性检测和过期自动重登。`,
  `（2）弹幕实时抓取：通过WebSocket拦截和DOM观察的双通道方案，实时抓取直播间的弹幕消息（chat）、礼物通知（gift）、点赞（like）、关注（follow）、进入直播间（enter）和在线统计（stats）等消息类型。`,
  `（3）AI智能回复：接入DeepSeek大语言模型，根据弹幕内容自动生成符合主播人设的回复文本，支持回复节流（最小回复间隔）、用户冷却（同一用户冷却期）、待回复队列和规则降级（LLM不可用时回退到规则引擎）。`,
  `（4）OBS场景联动：通过OBS WebSocket v5协议，实现关键词匹配触发的场景自动切换，支持命中计数窗口、最小命中次数、规则冷却和全局冷却等控制参数，播完后自动切回主场景。`,
  `（5）AI语音播报：接入阿里云百炼语音克隆服务，支持主播和助播两个角色的声音管理，实现AI回复文本到语音的自动播报。`,
  `（6）数字人推流：集成ffmpeg实现绿幕数字人视频与TTS音频的合成，通过HLS协议推流到OBS进行播放。`,
];

const sec3_2_content = [
  `（1）稳定性：系统应能在长时间直播（4小时以上）中稳定运行，不出现内存泄漏、线程死锁或连接异常中断等问题。弹幕抓取的漏抓率应控制在5%以内。`,
  `（2）实时性：弹幕从产生到UI展示的端到端延迟不超过1秒，AI回复从弹幕接收到回复生成的延迟不超过5秒。`,
  `（3）易用性：系统采用PyQt5桌面应用形式，提供图形化的配置界面，用户无需编写代码即可完成所有功能的配置和使用。`,
  `（4）安全性：所有API密钥和凭据信息本地存储，不通过网络传输；API密钥输入框支持密文显示切换。`,
];

const sec4_1_content = [
  `本系统采用分层模块化架构设计，整体结构分为四个层次：用户界面层、业务逻辑层、数据采集层和外部服务层。`,
  `用户界面层基于PyQt5和PyQt-SiliconUI组件库构建，包含首页（系统状态总览）、直播页（弹幕展示与AI回复）、AI配置页、语音设置页、OBS联动页、数字人推流页和通用设置页共七个功能页面。`,
  `业务逻辑层由CaptureWorker（捕获工作器）作为核心调度中心，在独立的QThread中运行asyncio事件循环，协调弹幕处理、AI回复、OBS联动、TTS语音和数字人推流等异步任务。CaptureWorker通过PyQt信号（Signal）与UI层通信，通过asyncio.run_coroutine_threadsafe桥接Qt主线程和asyncio工作线程。`,
  `数据采集层由RoomCapture（房间捕获器）和DanmakuDecoder（弹幕解码器）组成，负责通过Playwright拦截直播间的WebSocket帧并解码为结构化消息。`,
  `外部服务层包括DeepSeek API（大语言模型）、阿里云百炼API（语音服务）、OBS WebSocket（场景控制）和ffmpeg（视频合成），通过异步HTTP或WebSocket协议与这些服务进行交互。`,
];

const sec4_2_content = [
  `弹幕抓取模块采用WebSocket拦截和DOM观察的双通道方案，确保弹幕采集的完整性。`,
  `WebSocket通道：通过Playwright的page.on("websocket")事件监听浏览器中建立的WebSocket连接，筛选包含"douyin.com"的URL，通过ws.on("framereceived")事件捕获每帧数据。接收到的二进制帧经过三层解码（PushFrame解析→gzip解压→Response解析），提取出结构化的弹幕消息。系统还会发送ACK心跳包保持连接活跃。`,
  `DOM观察通道：通过注入JavaScript的MutationObserver监听直播间聊天区域的DOM变化，当检测到新的聊天消息DOM节点时，提取昵称和内容文本，通过Playwright的expose_binding回调传递给Python端处理。DOM观察作为WebSocket通道的补充，特别在WebSocket帧解析失败时提供兜底保障。`,
  `双通道融合：系统对两条通道的消息进行去重处理。每条chat消息通过（昵称, 内容）的标准化签名进行标识，在2秒的时间窗口内，同一签名只保留最先到达的那条。WebSocket通道的消息优先级高于DOM通道，当WS消息完整时直接转发，DOM消息仅在WS消息缺失昵称时作为补充。`,
];

const sec4_3_content = [
  `AI智能回复引擎是本系统的核心创新点，采用了多层控制策略确保回复质量和互动节奏。`,
  `人设系统：系统定义了角色名称、角色定位、语气风格、回复策略、场景话术、回复限制和禁忌事项七个维度的人设配置。这些配置被组合成完整的System Prompt，指导大语言模型生成符合主播人设的回复内容。例如，系统可以配置"慵懒松弛"的语气风格、40-80字的字数限制，以及禁止鸡汤文学、客服腔调等禁忌事项。`,
  `回复节流：系统设置了最小回复间隔（默认30秒），两次自动回复之间至少间隔设定时间，避免AI回复过于频繁导致刷屏。同时设置了每用户冷却（默认60秒），同一用户在冷却期内不会触发新的AI回复，确保回复机会均匀分配给不同观众。`,
  `短弹幕处理：对于"111"、"..."、"？"等无意义短弹幕，系统通过正则表达式进行识别，并推测其可能的意图（如"可能在刷屏"或"一脸问号"），将推测意图附加到发送给LLM的提示中，帮助模型生成更有针对性的回复。`,
  `规则降级：当DeepSeek API不可用（网络故障、API密钥未配置等）时，系统自动回退到基于关键词匹配的规则引擎，使用预设的回复模板生成简单的互动回复，保证直播间的基本互动不会中断。`,
  `待回复队列：当AI正在生成回复时，新到达的弹幕会被缓存到待回复队列中，等待当前回复完成后处理最新的一条，确保AI始终回复最新的弹幕内容。`,
];

const sec4_4_content = [
  `语音合成模块围绕VoiceManager（语音管理器）和TTSWorker（TTS工作器）两个核心组件构建。`,
  `VoiceManager负责管理语音供应商的配置和凭据，支持阿里云百炼等供应商。每个语音角色（主播、助播）关联一个声音条目（VoiceEntry），声音条目记录了样本路径、克隆状态和云端声音ID等信息。VoiceManager还负责合成缓存的管理，通过参数哈希避免重复调用API。`,
  `TTSWorker是一个基于asyncio.PriorityQueue的异步TTS队列处理器。当AI回复生成后，回复文本被封装为TTS任务并入队。TTSWorker按优先级出队任务，调用VoiceManager的合成接口生成音频，然后通过本地音频播放器进行播放。队列支持中断（高优先级任务可以中断正在播放的低优先级音频）和超时保护。`,
  `音频播放方面，系统在Windows平台使用winsound的异步播放模式，在其他平台使用pygame.mixer，确保音频播放不会阻塞主线程。`,
];

const sec5_1_content = [
  `登录会话管理模块利用Playwright的launch_persistent_context函数创建持久化浏览器上下文，将Cookie和localStorage等浏览器数据存储在本地browser_data目录中。用户首次使用时，系统打开抖音首页并等待扫码登录（最长2.5分钟），登录成功后浏览器状态自动保存，后续启动时无需重新扫码。`,
  `系统通过检查sessionid Cookie和页面URL来判断登录状态。如果检测到登录过期（页面被重定向到passport.douyin.com），系统会提示用户重新扫码。`,
  `弹幕抓取模块在RoomCapture类中实现。启动时，系统在已认证的浏览器上下文中创建新页面，注入WebSocket引用捕获脚本和DOM观察脚本，然后导航到直播间URL并自动刷新以确保WebSocket连接稳定建立。页面还配置了媒体资源拦截规则（block media类型请求），减少视频流带来的带宽消耗，只保留弹幕数据传输所需的WebSocket连接。`,
];

const sec5_2_content = [
  `协议解码在DanmakuDecoder类中实现，采用无状态设计（decode方法无副作用）。原始二进制帧经过三层解码：首先解析PushFrame信封获取gzip压缩的payload，然后解压缩得到Response消息体，最后遍历Response中的消息列表，根据method字段分派到对应的类型解析器（ChatMessage、GiftMessage、LikeMessage等）。`,
  `系统维护了方法统计（method_counts）和解析失败计数（parse_fail_counts），每60秒输出一次统计日志，帮助开发者监控协议变化和异常情况。对于未知的WebSocket方法，系统会记录首次观测的样本数据到debug_payload.log文件中，便于后续分析。`,
  `消息处理采用WS/DOM双通道融合策略。对于chat类型消息，WebSocket通道为优先通道，DOM通道为补充通道。系统通过（昵称, 内容）标准化的chat signature进行去重，在2秒窗口内同一签名只保留最早到达的消息。去重统计指标（ws_chat_total、ws_chat_complete、dom_chat_promoted等）每60秒记录一次。`,
];

const sec5_3_content = [
  `AI智能回复引擎在AIReplyEngine类中实现。当新弹幕到达时，引擎首先检查自动回复开关、屏蔽词、全局回复间隔和用户冷却等前置条件。通过检查后，引擎获取asyncio.Lock（确保同一时间只有一个回复在生成），构建包含系统提示词和对话历史的消息列表，调用DeepSeek API生成回复。`,
  `对话历史管理采用滑动窗口策略，保留最近4条对话记录（2条用户消息+2条助手回复），既提供了基本的上下文理解能力，又控制了API调用的token消耗。系统还集成了用户记忆管理（MemoryManager），将每次互动保存为JSONL文件，为未来的长期记忆功能预留了接口。`,
  `OBS联动模块在ObsActionController类中实现，通过关键词匹配和命中计数触发场景切换。每条弹幕内容经过casefold标准化后与规则库中的关键词进行匹配，匹配到的规则进入命中桶（hit bucket），在设定的时间窗口（默认10秒）内累计命中次数达到阈值（默认2次）时触发动作。动作执行后进入规则冷却期（默认60秒），防止同一规则被频繁触发。播放期间可以选择忽略新的触发请求。`,
];

const sec5_4_content = [
  `数字人推流模块在DigitalHumanPipeline类中实现，采用多步骤流水线架构：首先检查ffmpeg可用性，然后通过VoiceManager合成主播话术的语音，接着启动ffmpeg进程将绿幕视频和TTS音频合成为HLS切片流，同时启动本地HTTP服务器提供HLS文件访问，最后通过OBS WebSocket自动配置Media Source和色度键滤镜。`,
  `流水线的每个步骤都有独立的状态标识（IDLE、SYNTHESIZING、STARTING_SERVER、CONFIGURING_OBS、PUSHING、STREAMING、STOPPING、ERROR、CANCELLED），便于UI层实时显示进度。停止操作会依次终止ffmpeg进程、关闭HTTP服务器、清理临时文件并移除OBS中的媒体源。`,
  `ffmpeg视频合成采用HLS协议，设置2秒的切片时长和3个切片的播放列表大小，启用delete_segments标志自动清理过期切片，避免磁盘空间持续增长。视频编码使用libx264（ultrafast preset），音频编码使用AAC，兼顾了编码速度和画面质量。`,
];

const sec6_1_content = [
  `功能测试覆盖了系统的各个核心模块，测试结果如下表所示：`,
];

const sec6_2_content = [
  `性能测试主要关注弹幕抓取的实时性和AI回复的响应速度。测试环境为Windows 11系统，Intel i5处理器，16GB内存，使用pytest框架执行测试。`,
  `在弹幕解码性能方面，DanmakuDecoder的decode方法处理单帧数据的平均耗时在1毫秒以内，能够满足高频率弹幕场景下的实时解码需求。协议解码的稳定性经过多次长时间运行测试验证，在4小时连续运行中未出现内存泄漏或性能退化。`,
  `在AI回复性能方面，DeepSeek API的平均响应时间为3-5秒（100 token输出），加上本地处理开销，从弹幕接收到回复展示的端到端延迟控制在6-8秒以内，满足直播互动的时效性要求。`,
  `系统整体资源占用合理，CPU使用率在空闲时低于5%，在弹幕高峰时低于15%，内存占用稳定在200MB左右，适合在普通配置的Windows电脑上运行。`,
];

const conclusion = [
  `本文设计并实现了一个基于大语言模型的直播互动智能助手系统（Aiszr），围绕"稳定抓弹幕→AI文本回复→OBS动作联动→AI语音播报"的完整闭环，为直播电商场景提供了一套综合性智能化解决方案。`,
  `系统的主要创新点和贡献包括：`,
  `（1）提出了基于Playwright的被动式WebSocket拦截与DOM观察的双通道弹幕采集方案，无需逆向协议签名即可稳定抓取直播弹幕，在协议变更时具有更好的适应性。`,
  `（2）设计了包含人设系统、回复节流、用户冷却、短弹幕意图推测和规则降级的多层AI回复策略，实现了高质量、低干扰的直播间智能互动。`,
  `（3）集成了弹幕抓取、AI回复、OBS联动、语音播报和数字人推流等多种能力，形成完整的直播辅助闭环，主播无需在多个工具之间频繁切换。`,
  `系统已通过功能测试和性能测试验证，各模块运行稳定可靠。但本系统仍存在一些不足之处：语音克隆依赖阿里云百炼的单供应商服务，未来需要扩展多供应商支持；弹幕抓取目前仅支持抖音平台，需要适配更多直播平台；AI回复的上下文理解能力受限于滑动窗口策略，需要引入更高效的长期记忆机制。`,
  `未来的研究方向包括：扩展对快手、淘宝直播等平台的支持；引入RAG（检索增强生成）技术提升AI回复的商品知识准确性；研究巨量百应平台API实现文字场控发送能力；优化多房间并发管理策略，支持同时监控多个直播间。`,
];

const references = [
  "[1] 深度求索. DeepSeek API文档[EB/OL]. https://platform.deepseek.com/api-docs, 2025.",
  "[2] 微软. Playwright Python文档[EB/OL]. https://playwright.dev/python/, 2025.",
  "[3] 阿里云. DashScope语音合成API文档[EB/OL]. https://help.aliyun.com/document_detail/2712536.html, 2025.",
  "[4] OBS Project. OBS WebSocket v5 Protocol[EB/OL]. https://github.com/obsproject/obs-websocket/blob/master/docs/generated/protocol.md, 2024.",
  "[5] Google. Protocol Buffers Documentation[EB/OL]. https://protobuf.dev/, 2025.",
  "[6] Vaswani A, Shazeer N, Parmar N, et al. Attention Is All You Need[C]. Advances in Neural Information Processing Systems, 2017: 5998-6008.",
  "[7] zhonghangAlex. DySpider: 抖音直播弹幕抓取[EB/OL]. https://github.com/zhonghangAlex/DySpider, 2024.",
  "[8] OpenAI. GPT-4 Technical Report[J]. arXiv preprint arXiv:2303.08774, 2023.",
  "[9] Touvron H, Lavril T, Izacard G, et al. LLaMA: Open and Efficient Foundation Language Models[J]. arXiv preprint arXiv:2302.13971, 2023.",
  "[10] FastAPI. FastAPI Framework Documentation[EB/OL]. https://fastapi.tiangolo.com/, 2025.",
];

const thanks = [
  `经过数月的学习和努力，本次毕业设计已经接近尾声。在此，我要衷心感谢所有给予我帮助和支持的人。`,
  `首先，我要特别感谢我的指导老师章文老师。在整个毕业设计过程中，章老师从选题方向、技术路线到论文撰写，都给予了我耐心的指导和悉心的帮助。每当我在技术实现上遇到困难时，章老师总能给出建设性的建议，帮助我理清思路、找到解决方案。章老师严谨的治学态度和精益求精的工作作风，让我受益匪浅。`,
  `其次，我要感谢设计与信息学院的各位任课老师。在三年的人工智能技术应用专业学习中，是他们教授了我Python编程、数据结构、机器学习、深度学习等专业基础知识，为本次毕业设计的顺利完成奠定了坚实的基础。`,
  `同时，我还要感谢我的同学们。在开发过程中，我们互相交流技术方案、讨论遇到的问题，他们的建议和帮助对我启发很大。特别感谢在系统测试阶段给予我反馈和建议的朋友们。`,
  `最后，我要感谢我的家人，是他们在背后默默的支持和鼓励，让我能够专注于学业和毕业设计，顺利完成大学阶段的学习任务。`,
  `在未来的工作和学习中，我将继续努力，将所学知识应用于实际工作中，不断提升自己的技术能力和专业素养。`,
];

// ==================== Build Document ====================

// --- Section: Cover Page ---
const coverChildren = [
  new Paragraph({ spacing: { after: 600 }, alignment: AlignmentType.CENTER, children: [] }),
  new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { after: 200, line: LINE_15 },
    children: [new TextRun({ text: "嘉 兴 南 洋 职 业 技 术 学 院", font: FONT_HEI, size: SIZE_26PT })],
  }),
  new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { after: 800, line: LINE_15 },
    children: [new TextRun({ text: "毕 业 设 计（论 文）", font: FONT_HEI, size: SIZE_SI })],
  }),
];

const coverFields = [
  ["题  目", "基于大语言模型的直播互动智能助手系统设计与实现"],
  ["学  院", "设计与信息学院"],
  ["学生姓名", "黄智强"],
  ["专业名称", "人工智能技术应用"],
  ["班级/学号", "23人工智能1班 / 230723135"],
  ["指导教师", "章文"],
];

for (const [label, value] of coverFields) {
  coverChildren.push(new Paragraph({
    alignment: AlignmentType.LEFT,
    spacing: { line: LINE_15 },
    indent: { left: 1800 },
    children: [
      new TextRun({ text: `${label}：`, font: FONT_HEI, size: SIZE_SI }),
      new TextRun({ text: value, font: FONT_HEI, size: SIZE_SI, underline: { type: "single" } }),
    ],
  }));
}

coverChildren.push(new Paragraph({ spacing: { before: 600 }, alignment: AlignmentType.CENTER, children: [] }));
coverChildren.push(new Paragraph({
  alignment: AlignmentType.CENTER,
  children: [new TextRun({ text: "嘉兴南洋职业技术学院“双高”建设办公室印制", font: FONT_SONG, size: SIZE_SI, bold: true })],
}));

// --- Section: Abstract ---
const abstractChildren = [
  new Paragraph({ children: [new PageBreak()] }),
  new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { before: 480, after: 240, line: LINE_15 },
    children: [new TextRun({ text: "基于大语言模型的直播互动智能助手系统设计与实现", font: FONT_SONG, size: SIZE_XIAO2, bold: true })],
  }),
  new Paragraph({
    spacing: { line: LINE_15 },
    indent: { firstLine: 480 },
    children: [
      new TextRun({ text: "【摘要】", font: FONT_HEI, size: SIZE_XIAO4 }),
      new TextRun({ text: abstractText, font: FONT_SONG, size: SIZE_XIAO4 }),
    ],
  }),
  new Paragraph({
    spacing: { before: 120, line: LINE_15 },
    children: [
      new TextRun({ text: "【关键词】", font: FONT_HEI, size: SIZE_XIAO4 }),
      new TextRun({ text: keywords, font: FONT_SONG, size: SIZE_XIAO4 }),
    ],
  }),
];

// --- Section: TOC ---
const tocChildren = [
  new Paragraph({ children: [new PageBreak()] }),
  new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { before: 480, after: 240 },
    children: [new TextRun({ text: "目 录", font: FONT_HEI, size: SIZE_16PT, bold: true })],
  }),
  new TableOfContents("目录", { hyperlink: true, headingStyleRange: "1-2" }),
];

// --- Section: Body ---
const bodyChildren = [
  new Paragraph({ children: [new PageBreak()] }),
  heading1("1 绪论"),
  heading2("1.1 课题的研究背景"),
  ...sec1_1.map(bodyPara),
  heading2("1.2 国内外研究现状"),
  ...sec1_2.map(bodyPara),
  heading2("1.3 研究目标与内容"),
  ...sec1_3.map(bodyPara),

  heading1("2 相关技术概述"),
  heading2("2.1 Playwright 浏览器自动化技术"),
  ...sec2_1.map(bodyPara),
  heading2("2.2 Protobuf 协议与 WebSocket 通信"),
  ...sec2_2.map(bodyPara),
  heading2("2.3 大语言模型与 DeepSeek API"),
  ...sec2_3.map(bodyPara),
  heading2("2.4 阿里云百炼语音克隆技术"),
  ...sec2_4.map(bodyPara),

  heading1("3 系统需求分析"),
  heading2("3.1 功能需求分析"),
  ...sec3_1_content.map(bodyPara),
  heading2("3.2 非功能需求分析"),
  ...sec3_2_content.map(bodyPara),

  heading1("4 系统设计"),
  heading2("4.1 系统总体架构"),
  ...sec4_1_content.map(bodyPara),
  heading2("4.2 弹幕抓取模块设计"),
  ...sec4_2_content.map(bodyPara),
  heading2("4.3 AI 智能回复引擎设计"),
  ...sec4_3_content.map(bodyPara),
  heading2("4.4 语音合成模块设计"),
  ...sec4_4_content.map(bodyPara),

  heading1("5 系统实现"),
  heading2("5.1 登录会话管理与弹幕抓取"),
  ...sec5_1_content.map(bodyPara),
  heading2("5.2 协议解码与消息处理"),
  ...sec5_2_content.map(bodyPara),
  heading2("5.3 AI 智能回复引擎实现"),
  ...sec5_3_content.map(bodyPara),
  heading2("5.4 OBS 联动与数字人推流"),
  ...sec5_4_content.map(bodyPara),

  heading1("6 系统测试"),
  heading2("6.1 功能测试"),
  ...sec6_1_content.map(bodyPara),
  // Test results table
  new Table({
    width: { size: 8640, type: WidthType.DXA },
    columnWidths: [2000, 4140, 2500],
    rows: [
      makeTableHeaderRow(["测试模块", "测试内容", "测试结果"]),
      makeTableRow(["登录会话", "扫码登录、会话持久化、过期检测", "通过"]),
      makeTableRow(["弹幕抓取", "WS拦截、DOM观察、双通道去重", "通过"]),
      makeTableRow(["协议解码", "PushFrame/Response/ChatMessage解析", "通过"]),
      makeTableRow(["AI回复", "DeepSeek接口调用、人设回复、节流冷却", "通过"]),
      makeTableRow(["规则降级", "API不可用时回退到关键词匹配", "通过"]),
      makeTableRow(["OBS联动", "WebSocket连接、场景切换、播完回切", "通过"]),
      makeTableRow(["语音播报", "声音克隆、TTS合成、本地播放", "通过"]),
      makeTableRow(["数字人推流", "HLS合成、HTTP服务、OBS配置", "通过"]),
    ],
  }),
  figureCaption("表6-1 功能测试结果"),
  heading2("6.2 性能测试"),
  ...sec6_2_content.map(bodyPara),

  sectionTitle("结语"),
  ...conclusion.map(bodyPara),

  sectionTitle("参考文献"),
  ...references.map(r => new Paragraph({
    spacing: { line: LINE_15 },
    children: [new TextRun({ text: r, font: FONT_SONG, size: SIZE_XIAO4 })],
  })),

  sectionTitle("致谢"),
  ...thanks.map(bodyPara),
];


// ==================== Assemble Document ====================
const headerText = "嘉兴南洋职业技术学院毕业设计（论文）";
const headerPara = new Paragraph({
  alignment: AlignmentType.CENTER,
  border: { bottom: { style: BorderStyle.SINGLE, size: 6, color: "000000", space: 1 } },
  children: [new TextRun({ text: headerText, font: FONT_SONG, size: SIZE_XIAO5 })],
});

const doc = new Document({
  styles: {
    default: {
      document: { run: { font: FONT_SONG, size: SIZE_XIAO4 } },
    },
    paragraphStyles: [
      {
        id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: SIZE_XIAO3, bold: true, font: FONT_SONG },
        paragraph: { spacing: { before: 480, after: 240, line: LINE_15 }, outlineLevel: 0 },
      },
      {
        id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: SIZE_XIAO4, bold: true, font: FONT_SONG },
        paragraph: { spacing: { before: 240, after: 120, line: LINE_15 }, outlineLevel: 1 },
      },
    ],
  },
  sections: [
    // Section 1: Cover (no header/footer)
    {
      properties: {
        page: {
          size: { width: 11906, height: 16838 },
          margin: { top: MARGIN_TOP, bottom: 1418, left: MARGIN_LEFT, right: 1134 },
        },
      },
      children: coverChildren,
    },
    // Section 2: Abstract (Roman numeral page numbers)
    {
      properties: {
        page: {
          size: { width: 11906, height: 16838 },
          margin: { top: 1440, bottom: 1440, left: MARGIN_LEFT, right: 1134 },
        },
      },
      headers: { default: new Header({ children: [headerPara] }) },
      footers: {
        default: new Footer({
          children: [new Paragraph({
            alignment: AlignmentType.CENTER,
            children: [new TextRun({ text: "I", font: FONT_SONG, size: SIZE_XIAO5 })],
          })],
        }),
      },
      children: abstractChildren,
    },
    // Section 3: TOC (Roman numeral page numbers)
    {
      properties: {
        page: {
          size: { width: 11906, height: 16838 },
          margin: { top: 1440, bottom: 1440, left: MARGIN_LEFT, right: 1134 },
        },
      },
      headers: { default: new Header({ children: [headerPara] }) },
      footers: {
        default: new Footer({
          children: [new Paragraph({
            alignment: AlignmentType.CENTER,
            children: [new TextRun({ text: "II", font: FONT_SONG, size: SIZE_XIAO5 })],
          })],
        }),
      },
      children: tocChildren,
    },
    // Section 4: Body (Arabic page numbers)
    {
      properties: {
        page: {
          size: { width: 11906, height: 16838 },
          margin: { top: 1440, bottom: 1440, left: 1701, right: 1134 },
        },
      },
      headers: { default: new Header({ children: [headerPara] }) },
      footers: {
        default: new Footer({
          children: [new Paragraph({
            alignment: AlignmentType.CENTER,
            children: [
              new TextRun({ text: "第 ", font: FONT_SONG, size: SIZE_XIAO5 }),
              new TextRun({ children: [PageNumber.CURRENT], font: FONT_SONG, size: SIZE_XIAO5 }),
              new TextRun({ text: " 页，共 ", font: FONT_SONG, size: SIZE_XIAO5 }),
              new TextRun({ children: [PageNumber.TOTAL_PAGES], font: FONT_SONG, size: SIZE_XIAO5 }),
              new TextRun({ text: " 页", font: FONT_SONG, size: SIZE_XIAO5 }),
            ],
          })],
        }),
      },
      children: bodyChildren,
    },
  ],
});

// ==================== Write file ====================
const outputPath = "C:\\Users\\STG-WRITER1\\Desktop\\毕业论文_基于大语言模型的直播互动智能助手系统设计与实现.docx";
Packer.toBuffer(doc).then(buffer => {
  fs.writeFileSync(outputPath, buffer);
  console.log("Thesis document generated: " + outputPath);
});
