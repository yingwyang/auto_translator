"""
AstrBot Auto Translator Plugin
自动翻译插件 - 机器人发送消息时自动翻译成指定语言
"""

from astrbot.api import logger
from astrbot.api.star import Context, Star
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Plain
from astrbot.core.message.message_event_result import MessageChain
import aiohttp

# 尝试导入本地翻译库
try:
    import argostranslate.package
    import argostranslate.translate
    ARGOS_AVAILABLE = True
except ImportError:
    ARGOS_AVAILABLE = False
    logger.warning("[AutoTranslator] argostranslate 未安装，本地翻译功能不可用")


class AutoTranslator(Star):
    """自动翻译插件主类"""
    
    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)
        self.config = config or {}
        
        # 从配置中读取设置
        self.enable_auto_translate = self.config.get("enable_auto_translate", True)
        self.source_lang = self.config.get("source_lang", "zh")  # 源语言
        self.target_lang = self.config.get("target_lang", "en")  # 目标语言
        self.show_original = self.config.get("show_original", True)  # 是否显示原文
        self.use_llm_translate = self.config.get("use_llm_translate", False)  # 是否使用 LLM 翻译
        
        # 存储待翻译的文本
        self._pending_translation = {}
        
        # 初始化本地翻译模型（延迟初始化）
        self._local_translator = None
        self._local_translator_initialized = False
        
        logger.info(f"[AutoTranslator] 插件已加载，{self.source_lang} -> {self.target_lang}")
    
    def _ensure_local_translator(self):
        """确保本地翻译模型已初始化"""
        if not ARGOS_AVAILABLE:
            raise Exception("argostranslate 未安装")
        
        if self._local_translator_initialized:
            return
        
        try:
            # 安装语言包
            available_packages = argostranslate.package.get_available_packages()
            
            # 安装日语到中文的包
            for pkg in available_packages:
                if pkg.from_code == "ja" and pkg.to_code == "zh":
                    argostranslate.package.install_from_path(pkg.download())
                    logger.info("[AutoTranslator] 已安装日语到中文翻译包")
                    break
            
            # 安装英语到中文的包
            for pkg in available_packages:
                if pkg.from_code == "en" and pkg.to_code == "zh":
                    argostranslate.package.install_from_path(pkg.download())
                    logger.info("[AutoTranslator] 已安装英语到中文翻译包")
                    break
            
            self._local_translator_initialized = True
            logger.info("[AutoTranslator] 本地翻译模型初始化完成")
        except Exception as e:
            logger.error(f"[AutoTranslator] 本地翻译初始化失败: {e}")
            raise
    
    async def translate_text(self, text: str, from_lang: str, to_lang: str) -> str:
        """使用多个翻译 API 翻译文本，带备用方案"""
        if not text or not text.strip():
            return text
        
        # 尝试多个翻译源
        if self.use_llm_translate:
            # 优先使用 LLM 翻译（质量更好但更慢）
            translators = [
                self._translate_llm,
                self._translate_local,
                self._translate_bing,
                self._translate_google,
                self._translate_mymemory,
                self._translate_libre
            ]
        else:
            # 使用快速翻译源（速度优先）
            translators = [
                self._translate_local,
                self._translate_bing,
                self._translate_google,
                self._translate_mymemory,
                self._translate_libre
            ]
        
        for translator in translators:
            try:
                result = await translator(text, from_lang, to_lang)
                if result and result != text:
                    return result
            except Exception as e:
                logger.warning(f"[AutoTranslator] {translator.__name__} 失败: {e}")
                continue
        
        logger.error("[AutoTranslator] 所有翻译源都失败了")
        return text
    
    async def _translate_google(self, text: str, from_lang: str, to_lang: str) -> str:
        """Google 翻译 API"""
        url = "https://translate.googleapis.com/translate_a/single"
        params = {
            "client": "gtx",
            "sl": from_lang,
            "tl": to_lang,
            "dt": "t",
            "q": text
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, timeout=5) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    translated = "".join([item[0] for item in data[0] if item[0]])
                    return translated
                else:
                    raise Exception(f"API 返回错误: {resp.status}")
    
    async def _translate_mymemory(self, text: str, from_lang: str, to_lang: str) -> str:
        """MyMemory 翻译 API（备用）"""
        url = "https://api.mymemory.translated.net/get"
        params = {
            "q": text,
            "langpair": f"{from_lang}|{to_lang}"
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, timeout=5) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get("responseStatus") == 200:
                        return data["responseData"]["translatedText"]
                    else:
                        raise Exception(f"MyMemory API 错误: {data.get('responseDetails')}")
                else:
                    raise Exception(f"HTTP 错误: {resp.status}")
    
    async def _translate_libre(self, text: str, from_lang: str, to_lang: str) -> str:
        """LibreTranslate 翻译 API（备用）"""
        url = "https://libretranslate.de/translate"
        headers = {"Content-Type": "application/json"}
        data = {
            "q": text,
            "source": from_lang,
            "target": to_lang,
            "format": "text"
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=data, timeout=5) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    return result.get("translatedText", text)
                else:
                    raise Exception(f"HTTP 错误: {resp.status}")
    
    async def _translate_bing(self, text: str, from_lang: str, to_lang: str) -> str:
        """微软 Bing 翻译 API（国内可访问）"""
        # 使用 Bing 翻译网页版 API
        url = "https://cn.bing.com/ttranslatev3"
        
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer": "https://cn.bing.com/translator"
        }
        
        # 转换语言代码
        lang_map = {
            "zh": "zh-Hans",
            "en": "en",
            "ja": "ja",
            "ko": "ko",
            "fr": "fr",
            "de": "de",
            "es": "es",
            "ru": "ru"
        }
        
        from_lang_mapped = lang_map.get(from_lang, from_lang)
        to_lang_mapped = lang_map.get(to_lang, to_lang)
        
        data = {
            "fromLang": from_lang_mapped,
            "to": to_lang_mapped,
            "text": text
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, data=data, timeout=5) as resp:
                if resp.status == 200:
                    text_content = await resp.text()
                    # 尝试解析 JSON
                    try:
                        import json
                        result = json.loads(text_content)
                        if isinstance(result, list) and len(result) > 0:
                            translations = result[0].get("translations", [])
                            if translations:
                                return translations[0].get("text", text)
                    except json.JSONDecodeError:
                        # 如果不是 JSON，可能是直接的文本
                        if text_content and text_content != text:
                            return text_content
                    raise Exception(f"Bing API 返回格式异常: {text_content[:100]}")
                else:
                    raise Exception(f"Bing API HTTP 错误: {resp.status}")
    
    async def _translate_local(self, text: str, from_lang: str, to_lang: str) -> str:
        """使用本地 Argos Translate 翻译（无需网络）"""
        # 确保本地翻译模型已初始化
        self._ensure_local_translator()
        
        # Argos 使用两位语言代码
        lang_map = {
            "zh": "zh",
            "en": "en",
            "ja": "ja",
            "ko": "ko",
            "fr": "fr",
            "de": "de",
            "es": "es",
            "ru": "ru"
        }
        
        from_code = lang_map.get(from_lang, from_lang)
        to_code = lang_map.get(to_lang, to_lang)
        
        # 执行翻译
        translated = argostranslate.translate.translate(text, from_code, to_code)
        
        if translated and translated != text:
            logger.info(f"[AutoTranslator] 本地翻译成功: {text[:30]}... -> {translated[:30]}...")
            return translated
        else:
            raise Exception("本地翻译失败或返回原文")
    
    async def _translate_llm(self, text: str, from_lang: str, to_lang: str) -> str:
        """使用 LLM 进行高质量翻译"""
        # 获取 provider
        provider = self.context.get_provider_by_id("llm")
        if not provider:
            raise Exception("LLM provider 不可用")
        
        # 构建翻译提示
        lang_names = {
            "zh": "中文",
            "en": "英文",
            "ja": "日文",
            "ko": "韩文",
            "fr": "法文",
            "de": "德文",
            "es": "西班牙文",
            "ru": "俄文"
        }
        
        from_lang_name = lang_names.get(from_lang, from_lang)
        to_lang_name = lang_names.get(to_lang, to_lang)
        
        prompt = f"""请将以下{from_lang_name}翻译成{to_lang_name}。要求：
1. 保持原文的语气和情感
2. 翻译要自然流畅，符合目标语言的表达习惯
3. 只返回翻译结果，不要添加任何解释

原文：{text}

翻译："""
        
        # 调用 LLM
        response = await provider.text_chat(
            prompt=prompt,
            session_id="translate_temp"
        )
        
        if response and response.completion_text:
            translated = response.completion_text.strip()
            if translated and translated != text:
                logger.info(f"[AutoTranslator] LLM 翻译成功")
                return translated
        
        raise Exception("LLM 翻译失败")
    
    @filter.on_llm_response()
    async def on_llm_response(self, event: AstrMessageEvent, response):
        """在 LLM 响应后翻译并发送翻译消息"""
        if not self.enable_auto_translate:
            return
        
        try:
            # 处理 LLMResponse 对象，提取文本内容
            if hasattr(response, 'completion_text'):
                original_text = response.completion_text
            elif hasattr(response, 'text'):
                original_text = response.text
            else:
                original_text = str(response)
            
            if not original_text or not original_text.strip():
                return
            
            # 执行翻译
            translated = await self.translate_text(
                original_text,
                self.source_lang,
                self.target_lang
            )
            
            if translated == original_text:
                return
            
            # 发送翻译消息
            if self.show_original:
                translate_text = f"原文：{original_text}\n翻译：{translated}"
            else:
                translate_text = f"翻译：{translated}"
            
            # 使用 event.send 发送翻译消息（需要 MessageChain）
            message_chain = MessageChain().message(translate_text)
            await event.send(message_chain)
            logger.info(f"[AutoTranslator] 翻译已发送: {translated[:50]}...")
            
        except Exception as e:
            import traceback
            logger.error(f"[AutoTranslator] 翻译发送失败: {e}")
            logger.error(f"[AutoTranslator] 错误详情: {traceback.format_exc()}")
    
    @filter.command("翻译设置")
    async def set_translate(self, event: AstrMessageEvent):
        """设置翻译参数"""
        msg = event.message_str.strip()
        parts = msg.split()
        
        if len(parts) < 3:
            yield event.plain_result(
                "用法: 翻译设置 <源语言> <目标语言>\n"
                "例如: 翻译设置 zh en (中文转英文)\n"
                "语言代码: zh(中文), en(英文), ja(日文), ko(韩文)等"
            )
            return
        
        self.source_lang = parts[1]
        self.target_lang = parts[2]
        
        yield event.plain_result(
            f"✅ 翻译设置已更新: {self.source_lang} -> {self.target_lang}"
        )
    
    @filter.command("关闭翻译")
    async def disable_translate(self, event: AstrMessageEvent):
        self.enable_auto_translate = False
        yield event.plain_result("❌ 自动翻译已关闭")
    
    @filter.command("开启翻译")
    async def enable_translate(self, event: AstrMessageEvent):
        self.enable_auto_translate = True
        yield event.plain_result(f"✅ 自动翻译已开启")


def create_star(context: Context, config: dict):
    return AutoTranslator(context, config)