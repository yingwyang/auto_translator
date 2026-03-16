"""
AstrBot Auto Translator Plugin
自动翻译插件 - 机器人发送消息时自动翻译成指定语言
"""

from astrbot.api import logger
from astrbot.api.star import Context, Star
from astrbot.api.event import AstrMessageEvent, filter
import aiohttp


class AutoTranslator(Star):
    """自动翻译插件主类"""
    
    def __init__(self, context: Context, config: dict):
        super().__init__(context)
        self.config = config
        
        # 从配置中读取设置
        self.enable_auto_translate = config.get("enable_auto_translate", True)
        self.source_lang = config.get("source_lang", "zh")  # 源语言
        self.target_lang = config.get("target_lang", "en")  # 目标语言
        self.show_original = config.get("show_original", True)  # 是否显示原文
        
        logger.info(f"[AutoTranslator] 插件已加载，{self.source_lang} -> {self.target_lang}")
    
    async def translate_text(self, text: str, from_lang: str, to_lang: str) -> str:
        """使用 Google 翻译 API 翻译文本"""
        if not text or not text.strip():
            return text
            
        try:
            url = "https://translate.googleapis.com/translate_a/single"
            params = {
                "client": "gtx",
                "sl": from_lang,
                "tl": to_lang,
                "dt": "t",
                "q": text
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, timeout=10) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        translated = "".join([item[0] for item in data[0] if item[0]])
                        return translated
                    else:
                        raise Exception(f"API 返回错误: {resp.status}")
        except Exception as e:
            logger.error(f"[AutoTranslator] 翻译失败: {e}")
            return text
    
    @filter.on_llm_response()
    async def on_llm_response(self, event: AstrMessageEvent, response: str):
        """在 LLM 响应后自动翻译"""
        if not self.enable_auto_translate:
            return
        
        try:
            original_text = response
            if not original_text:
                return
            
            translated = await self.translate_text(
                original_text, 
                self.source_lang, 
                self.target_lang
            )
            
            if translated == original_text:
                return
            
            if self.show_original:
                final_text = f"{original_text}\n\n[翻译] {translated}"
            else:
                final_text = translated
            
            event.set_result(final_text)
            logger.info(f"[AutoTranslator] 翻译完成")
            
        except Exception as e:
            logger.error(f"[AutoTranslator] 处理失败: {e}")
    
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