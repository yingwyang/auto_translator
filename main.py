"""
AstrBot Auto Translator Plugin
自动翻译插件 - 机器人发送消息时自动翻译成指定语言
"""

from astrbot.api import logger
from astrbot.api.star import Context, Star
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.core.message.message_event_result import MessageChain
import aiohttp
import asyncio
import uuid

# 尝试导入本地翻译库
try:
    import argostranslate.package
    import argostranslate.translate
    ARGOS_AVAILABLE = True
except ImportError:
    ARGOS_AVAILABLE = False
    logger.warning("[AutoTranslator] argostranslate 未安装，本地翻译功能不可用")

# 支持的语言代码
SUPPORTED_LANGS = {"zh", "en", "ja", "ko", "fr", "de", "es", "ru", "pt", "it", "nl", "pl", "tr", "ar", "th", "vi"}


class AutoTranslator(Star):
    """自动翻译插件主类"""
    
    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)
        self.config = config or {}
        
        # 从配置中读取设置
        self.enable_auto_translate = self.config.get("enable_auto_translate", True)
        self.source_lang = self.config.get("source_lang", "ja")
        self.target_lang = self.config.get("target_lang", "zh")
        self.show_original = self.config.get("show_original", True)
        self.use_llm_translate = self.config.get("use_llm_translate", False)
        
        # 本地翻译模型状态
        self._local_translator_initialized = False
        self._installed_packages = set()  # 记录已安装的语言包
        
        # 复用 aiohttp session
        self._session = None
        
        logger.info(f"[AutoTranslator] 插件已加载，{self.source_lang} -> {self.target_lang}")
    
    async def _get_session(self) -> aiohttp.ClientSession:
        """获取复用的 aiohttp session"""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session
    
    async def _ensure_local_translator(self, from_lang: str, to_lang: str):
        """确保本地翻译模型已初始化（异步包装）"""
        if not ARGOS_AVAILABLE:
            raise Exception("argostranslate 未安装")
        
        # 检查是否已安装所需语言包
        lang_pair = (from_lang, to_lang)
        if lang_pair in self._installed_packages:
            return
        
        # 使用 to_thread 包装同步阻塞调用
        try:
            await asyncio.to_thread(self._install_language_package, from_lang, to_lang)
            self._installed_packages.add(lang_pair)
            self._local_translator_initialized = True
        except Exception as e:
            logger.error(f"[AutoTranslator] 安装语言包失败 {from_lang}->{to_lang}: {e}")
            raise
    
    def _install_language_package(self, from_lang: str, to_lang: str):
        """安装语言包（同步方法，在后台线程执行）"""
        available_packages = argostranslate.package.get_available_packages()
        
        # 查找并安装所需语言包
        for pkg in available_packages:
            if pkg.from_code == from_lang and pkg.to_code == to_lang:
                logger.info(f"[AutoTranslator] 正在安装语言包 {from_lang}->{to_lang}")
                argostranslate.package.install_from_path(pkg.download())
                logger.info(f"[AutoTranslator] 已安装语言包 {from_lang}->{to_lang}")
                return
        
        raise Exception(f"未找到语言包 {from_lang}->{to_lang}")
    
    async def translate_text(self, text: str, from_lang: str, to_lang: str) -> str:
        """使用多个翻译 API 翻译文本，带备用方案"""
        if not text or not text.strip():
            return text
        
        # 尝试多个翻译源
        if self.use_llm_translate:
            translators = [
                ("llm", self._translate_llm),
                ("local", self._translate_local),
                ("bing", self._translate_bing),
                ("google", self._translate_google),
            ]
        else:
            translators = [
                ("local", self._translate_local),
                ("bing", self._translate_bing),
                ("google", self._translate_google),
                ("mymemory", self._translate_mymemory),
                ("libre", self._translate_libre),
            ]
        
        last_error = None
        for name, translator in translators:
            try:
                result = await translator(text, from_lang, to_lang)
                if result:
                    logger.info(f"[AutoTranslator] 使用 {name} 翻译成功")
                    return result
            except asyncio.TimeoutError:
                logger.warning(f"[AutoTranslator] {name} 翻译超时")
                last_error = "timeout"
            except aiohttp.ClientError as e:
                logger.warning(f"[AutoTranslator] {name} 网络错误: {e}")
                last_error = "network"
            except Exception as e:
                logger.warning(f"[AutoTranslator] {name} 翻译失败: {e}")
                last_error = str(e)
                continue
        
        logger.error(f"[AutoTranslator] 所有翻译源都失败了，最后错误: {last_error}")
        return text  # 返回原文作为fallback
    
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
        
        session = await self._get_session()
        async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=5)) as resp:
            if resp.status == 200:
                data = await resp.json()
                if isinstance(data, list) and len(data) > 0 and isinstance(data[0], list):
                    translated = "".join([item[0] for item in data[0] if isinstance(item, list) and len(item) > 0])
                    return translated
                raise ValueError(f"Google API 返回格式异常: {type(data)}")
            else:
                raise aiohttp.ClientResponseError(
                    resp.request_info, resp.history, status=resp.status
                )
    
    async def _translate_mymemory(self, text: str, from_lang: str, to_lang: str) -> str:
        """MyMemory 翻译 API（备用）"""
        url = "https://api.mymemory.translated.net/get"
        params = {
            "q": text,
            "langpair": f"{from_lang}|{to_lang}"
        }
        
        session = await self._get_session()
        async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=5)) as resp:
            if resp.status == 200:
                data = await resp.json()
                if data.get("responseStatus") == 200:
                    return data["responseData"]["translatedText"]
                else:
                    raise ValueError(f"MyMemory API 错误: {data.get('responseDetails')}")
            else:
                raise aiohttp.ClientResponseError(
                    resp.request_info, resp.history, status=resp.status
                )
    
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
        
        session = await self._get_session()
        async with session.post(url, headers=headers, json=data, timeout=aiohttp.ClientTimeout(total=5)) as resp:
            if resp.status == 200:
                result = await resp.json()
                return result.get("translatedText", text)
            else:
                raise aiohttp.ClientResponseError(
                    resp.request_info, resp.history, status=resp.status
                )
    
    async def _translate_bing(self, text: str, from_lang: str, to_lang: str) -> str:
        """微软 Bing 翻译 API（国内可访问）"""
        url = "https://cn.bing.com/ttranslatev3"
        
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer": "https://cn.bing.com/translator"
        }
        
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
        
        session = await self._get_session()
        async with session.post(url, headers=headers, data=data, timeout=aiohttp.ClientTimeout(total=5)) as resp:
            if resp.status == 200:
                text_content = await resp.text()
                
                # 严格验证返回内容
                if not text_content or text_content.startswith("<"):
                    raise ValueError(f"Bing API 返回非预期内容: {text_content[:100]}")
                
                try:
                    import json
                    result = json.loads(text_content)
                    if isinstance(result, list) and len(result) > 0:
                        translations = result[0].get("translations", [])
                        if translations and isinstance(translations[0], dict):
                            translated = translations[0].get("text")
                            if translated:
                                return translated
                    raise ValueError(f"Bing API 返回结构异常")
                except json.JSONDecodeError as e:
                    raise ValueError(f"Bing API JSON 解析失败: {e}")
            else:
                raise aiohttp.ClientResponseError(
                    resp.request_info, resp.history, status=resp.status
                )
    
    async def _translate_local(self, text: str, from_lang: str, to_lang: str) -> str:
        """使用本地 Argos Translate 翻译（无需网络）"""
        await self._ensure_local_translator(from_lang, to_lang)
        
        # 在后台线程执行翻译
        translated = await asyncio.to_thread(
            argostranslate.translate.translate, text, from_lang, to_lang
        )
        
        if translated:
            return translated
        else:
            raise ValueError("本地翻译返回空结果")
    
    async def _translate_llm(self, text: str, from_lang: str, to_lang: str) -> str:
        """使用 LLM 进行高质量翻译"""
        provider = self.context.get_provider_by_id("llm")
        if not provider:
            raise Exception("LLM provider 不可用")
        
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
        
        # 使用唯一的 session_id
        session_id = f"translate_{uuid.uuid4().hex[:8]}"
        
        response = await provider.text_chat(
            prompt=prompt,
            session_id=session_id
        )
        
        if response and response.completion_text:
            translated = response.completion_text.strip()
            if translated:
                return translated
        
        raise ValueError("LLM 翻译返回空结果")
    
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
            
            if not translated:
                return
            
            # 发送翻译消息
            if self.show_original:
                translate_text = f"原文：{original_text}\n翻译：{translated}"
            else:
                translate_text = f"翻译：{translated}"
            
            message_chain = MessageChain().message(translate_text)
            await event.send(message_chain)
            logger.info(f"[AutoTranslator] 翻译已发送")
            
        except Exception as e:
            logger.error(f"[AutoTranslator] 翻译发送失败: {e}")
    
    def _validate_lang_code(self, lang_code: str) -> bool:
        """验证语言代码是否有效"""
        return lang_code in SUPPORTED_LANGS
    
    @filter.command("翻译设置")
    async def set_translate(self, event: AstrMessageEvent):
        """设置翻译参数"""
        msg = event.message_str.strip()
        parts = msg.split()
        
        if len(parts) < 3:
            yield event.plain_result(
                "用法: 翻译设置 <源语言> <目标语言>\n"
                "例如: 翻译设置 ja zh (日文转中文)\n"
                f"支持的语言代码: {', '.join(sorted(SUPPORTED_LANGS))}"
            )
            return
        
        from_lang = parts[1]
        to_lang = parts[2]
        
        # 验证语言代码
        if not self._validate_lang_code(from_lang):
            yield event.plain_result(f"❌ 不支持的源语言代码: {from_lang}\n支持的语言: {', '.join(sorted(SUPPORTED_LANGS))}")
            return
        
        if not self._validate_lang_code(to_lang):
            yield event.plain_result(f"❌ 不支持的目标语言代码: {to_lang}\n支持的语言: {', '.join(sorted(SUPPORTED_LANGS))}")
            return
        
        self.source_lang = from_lang
        self.target_lang = to_lang
        
        yield event.plain_result(
            f"✅ 翻译设置已更新: {from_lang} -> {to_lang}"
        )
    
    @filter.command("关闭翻译")
    async def disable_translate(self, event: AstrMessageEvent):
        self.enable_auto_translate = False
        yield event.plain_result("❌ 自动翻译已关闭")
    
    @filter.command("开启翻译")
    async def enable_translate(self, event: AstrMessageEvent):
        self.enable_auto_translate = True
        yield event.plain_result(f"✅ 自动翻译已开启 ({self.source_lang} -> {self.target_lang})")
    
    async def terminate(self):
        """插件卸载时清理资源"""
        if self._session and not self._session.closed:
            await self._session.close()
            logger.info("[AutoTranslator] 已关闭 aiohttp session")


def create_star(context: Context, config: dict):
    return AutoTranslator(context, config)
