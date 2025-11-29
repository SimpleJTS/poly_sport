"""
Telegram通知服务
"""

import asyncio
from typing import Optional
import httpx

from app.config import config_manager
from app.utils.logger import get_logger, LogMessages

logger = get_logger("telegram")


class TelegramNotifier:
    """Telegram消息通知服务"""
    
    BASE_URL = "https://api.telegram.org/bot{token}"
    
    def __init__(self):
        self._http_client: Optional[httpx.AsyncClient] = None
    
    async def initialize(self):
        """初始化"""
        self._http_client = httpx.AsyncClient(timeout=30.0)
    
    async def close(self):
        """关闭"""
        if self._http_client:
            await self._http_client.aclose()
    
    @property
    def is_configured(self) -> bool:
        """是否已配置"""
        cfg = config_manager.telegram
        return bool(cfg.enabled and cfg.bot_token and cfg.chat_id)
    
    async def send_message(self, text: str, parse_mode: str = "HTML") -> bool:
        """
        发送消息
        
        Args:
            text: 消息内容
            parse_mode: 解析模式 (HTML/Markdown)
        
        Returns:
            是否发送成功
        """
        if not self.is_configured:
            logger.debug("Telegram未配置，跳过发送")
            return False
        
        cfg = config_manager.telegram
        
        try:
            url = f"{self.BASE_URL.format(token=cfg.bot_token)}/sendMessage"
            
            response = await self._http_client.post(url, json={
                "chat_id": cfg.chat_id,
                "text": text,
                "parse_mode": parse_mode
            })
            
            if response.status_code == 200:
                logger.debug(LogMessages.TG_SEND_SUCCESS)
                return True
            else:
                logger.error(LogMessages.TG_SEND_FAILED.format(error=response.text))
                return False
                
        except Exception as e:
            logger.error(LogMessages.TG_SEND_FAILED.format(error=str(e)))
            return False
    
    async def test_connection(self) -> bool:
        """测试连接"""
        return await self.send_message("🔔 <b>Polymarket交易机器人</b>\n\n连接测试成功！")
    
    # ============ 预定义消息模板 ============
    
    async def notify_buy(self, market_question: str, price: float, amount: float, 
                        order_id: str = ""):
        """买入通知"""
        message = f"""
🟢 <b>买入成功</b>

📊 市场: {market_question[:100]}
💰 价格: {price:.2f}
💵 金额: ${amount:.2f}
🔖 订单ID: {order_id[:20] if order_id else 'N/A'}
⏰ 时间: {{time}}
""".format(time=self._get_time_str())
        
        await self.send_message(message)
    
    async def notify_sell(self, market_question: str, price: float, amount: float,
                         pnl: float = 0, reason: str = ""):
        """卖出通知"""
        pnl_emoji = "📈" if pnl >= 0 else "📉"
        pnl_sign = "+" if pnl >= 0 else ""
        
        message = f"""
🔴 <b>卖出成功</b>

📊 市场: {market_question[:100]}
💰 价格: {price:.2f}
💵 金额: ${amount:.2f}
{pnl_emoji} 盈亏: {pnl_sign}${pnl:.2f}
📝 原因: {reason or '手动卖出'}
⏰ 时间: {self._get_time_str()}
"""
        await self.send_message(message)
    
    async def notify_stop_loss(self, market_question: str, trigger_price: float,
                               entry_price: float, loss: float):
        """止损触发通知"""
        message = f"""
⚠️ <b>止损触发</b>

📊 市场: {market_question[:100]}
📍 入场价: {entry_price:.2f}
🎯 触发价: {trigger_price:.2f}
📉 亏损: -${abs(loss):.2f}
⏰ 时间: {self._get_time_str()}
"""
        await self.send_message(message)
    
    async def notify_price_alert(self, market_question: str, price: float,
                                  alert_type: str = "entry"):
        """价格提醒"""
        emoji = "🎯" if alert_type == "entry" else "⚠️"
        type_text = "入场价格" if alert_type == "entry" else "止损价格"
        
        message = f"""
{emoji} <b>{type_text}触发</b>

📊 市场: {market_question[:100]}
💰 当前价格: {price:.2f}
⏰ 时间: {self._get_time_str()}
"""
        await self.send_message(message)
    
    async def notify_error(self, error_message: str):
        """错误通知"""
        message = f"""
❌ <b>系统错误</b>

⚠️ {error_message}
⏰ 时间: {self._get_time_str()}
"""
        await self.send_message(message)
    
    async def notify_daily_summary(self, stats: dict):
        """每日总结"""
        pnl = stats.get('realized_pnl', 0)
        pnl_emoji = "📈" if pnl >= 0 else "📉"
        pnl_sign = "+" if pnl >= 0 else ""
        
        win_rate = 0
        total = stats.get('win_trades', 0) + stats.get('loss_trades', 0)
        if total > 0:
            win_rate = stats.get('win_trades', 0) / total * 100
        
        message = f"""
📊 <b>每日交易总结</b>

📅 日期: {stats.get('date', 'N/A')}
🔢 交易次数: {stats.get('total_trades', 0)}
💰 交易量: ${stats.get('total_volume', 0):.2f}
{pnl_emoji} 盈亏: {pnl_sign}${pnl:.2f}
📊 胜率: {win_rate:.1f}%
✅ 盈利: {stats.get('win_trades', 0)} 次
❌ 亏损: {stats.get('loss_trades', 0)} 次
"""
        await self.send_message(message)
    
    async def notify_system_start(self):
        """系统启动通知"""
        message = f"""
🚀 <b>交易系统启动</b>

✅ Polymarket尾盘交易策略已启动
⏰ 时间: {self._get_time_str()}
"""
        await self.send_message(message)
    
    async def notify_system_stop(self):
        """系统停止通知"""
        message = f"""
🛑 <b>交易系统停止</b>

⏰ 时间: {self._get_time_str()}
"""
        await self.send_message(message)
    
    def _get_time_str(self) -> str:
        """获取当前时间字符串"""
        from datetime import datetime
        return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")


# 全局通知实例
telegram_notifier = TelegramNotifier()
