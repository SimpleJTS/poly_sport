"""
配置管理模块
支持环境变量和运行时配置
"""

import os
import json
from pathlib import Path
from typing import Optional
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()


class PolymarketConfig(BaseModel):
    """Polymarket API配置"""
    private_key: str = Field(default="", description="钱包私钥")
    funder: str = Field(default="", description="资金持有者地址")
    
    # API端点
    host: str = Field(default="https://clob.polymarket.com", description="CLOB API地址")
    gamma_host: str = Field(default="https://gamma-api.polymarket.com", description="Gamma API地址")


class TelegramConfig(BaseModel):
    """Telegram配置"""
    enabled: bool = Field(default=False, description="是否启用Telegram通知")
    bot_token: str = Field(default="", description="Bot Token")
    chat_id: str = Field(default="", description="Chat ID")


class TradingConfig(BaseModel):
    """交易策略配置"""
    # 入场配置
    entry_price: float = Field(default=90.0, ge=0, le=100, description="入场价格（0-100）")
    
    # 止损配置
    stop_loss_price: float = Field(default=85.0, ge=0, le=100, description="止损价格（0-100）")
    
    # 金额配置
    order_amount: float = Field(default=10.0, ge=1, description="每次下单金额（USDC）")
    max_position_amount: float = Field(default=100.0, ge=1, description="最大持仓金额（USDC）")
    
    # 时间过滤
    time_filter_hours: float = Field(default=1.0, ge=0.1, description="尾盘时间过滤（小时）")
    
    # 扫描间隔
    scan_interval: int = Field(default=30, ge=5, description="市场扫描间隔（秒）")
    price_check_interval: int = Field(default=5, ge=1, description="价格检查间隔（秒）")
    
    # 风控配置
    max_daily_loss: float = Field(default=50.0, ge=0, description="每日最大亏损（USDC）")
    max_open_positions: int = Field(default=5, ge=1, description="最大同时持仓数量")
    
    # 自动交易开关
    auto_trading_enabled: bool = Field(default=False, description="是否启用自动交易")


class AppConfig(BaseSettings):
    """应用配置"""
    # 服务配置
    host: str = Field(default="0.0.0.0", description="服务监听地址")
    port: int = Field(default=9000, description="服务端口")
    debug: bool = Field(default=False, description="调试模式")
    
    # 日志配置
    log_dir: str = Field(default="logs", description="日志目录")
    
    # 数据目录
    data_dir: str = Field(default="data", description="数据目录")
    
    class Config:
        env_prefix = ""
        case_sensitive = False


class ConfigManager:
    """配置管理器 - 支持运行时修改和持久化"""
    
    def __init__(self, config_file: str = "data/config.json"):
        self.config_file = Path(config_file)
        self.config_file.parent.mkdir(parents=True, exist_ok=True)
        
        # 初始化配置
        self.app = AppConfig()
        self.polymarket = PolymarketConfig(
            private_key=os.getenv("POLY_PRIVATE_KEY", ""),
            funder=os.getenv("POLY_FUNDER", "")
        )
        self.telegram = TelegramConfig(
            enabled=bool(os.getenv("TG_BOT_TOKEN")),
            bot_token=os.getenv("TG_BOT_TOKEN", ""),
            chat_id=os.getenv("TG_CHAT_ID", "")
        )
        self.trading = TradingConfig()
        
        # 加载持久化配置
        self._load_config()
    
    def _load_config(self):
        """从文件加载配置"""
        if self.config_file.exists():
            try:
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    
                # 更新交易配置
                if 'trading' in data:
                    self.trading = TradingConfig(**data['trading'])
                
                # 更新Telegram配置（不包含敏感信息）
                if 'telegram' in data:
                    tg_data = data['telegram']
                    if 'enabled' in tg_data:
                        self.telegram.enabled = tg_data['enabled']
                    if 'bot_token' in tg_data and tg_data['bot_token']:
                        self.telegram.bot_token = tg_data['bot_token']
                    if 'chat_id' in tg_data and tg_data['chat_id']:
                        self.telegram.chat_id = tg_data['chat_id']
                        
            except Exception as e:
                print(f"加载配置文件失败: {e}")
    
    def save_config(self):
        """保存配置到文件"""
        data = {
            'trading': self.trading.model_dump(),
            'telegram': {
                'enabled': self.telegram.enabled,
                'bot_token': self.telegram.bot_token,
                'chat_id': self.telegram.chat_id
            }
        }
        
        with open(self.config_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    
    def update_trading_config(self, **kwargs):
        """更新交易配置"""
        current = self.trading.model_dump()
        current.update(kwargs)
        self.trading = TradingConfig(**current)
        self.save_config()
    
    def update_telegram_config(self, **kwargs):
        """更新Telegram配置"""
        current = {
            'enabled': self.telegram.enabled,
            'bot_token': self.telegram.bot_token,
            'chat_id': self.telegram.chat_id
        }
        current.update(kwargs)
        self.telegram = TelegramConfig(**current)
        self.save_config()

    def update_polymarket_config(self, **kwargs):
        """更新Polymarket配置"""
        current = {
            'private_key': self.polymarket.private_key,
            'funder': self.polymarket.funder
        }
        current.update(kwargs)
        self.polymarket = PolymarketConfig(**current)
        # Polymarket配置不保存到文件，只保存在环境变量中
        # 这里只更新内存中的配置

    def get_trading_config_dict(self) -> dict:
        """获取交易配置字典（用于前端显示）"""
        return self.trading.model_dump()

    def get_telegram_config_dict(self) -> dict:
        """获取Telegram配置字典（隐藏敏感信息）"""
        return {
            'enabled': self.telegram.enabled,
            'bot_token': self.telegram.bot_token[:10] + "***" if self.telegram.bot_token else "",
            'chat_id': self.telegram.chat_id,
            'configured': bool(self.telegram.bot_token and self.telegram.chat_id)
        }

    def get_polymarket_config_dict(self) -> dict:
        """获取Polymarket配置字典（隐藏敏感信息）"""
        return {
            'private_key': self.polymarket.private_key[:10] + "***" if self.polymarket.private_key else "",
            'funder': self.polymarket.funder,
            'funder_short': self.polymarket.funder[:10] + "..." + self.polymarket.funder[-8:] if self.polymarket.funder else "",
            'configured': bool(self.polymarket.private_key and self.polymarket.funder)
        }


# 全局配置实例
config_manager = ConfigManager()
