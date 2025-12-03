"""
数据模型定义
"""

from datetime import datetime
from typing import Optional, List
from enum import Enum
from pydantic import BaseModel, Field


class OrderSide(str, Enum):
    """订单方向"""
    BUY = "BUY"
    SELL = "SELL"


class OrderStatus(str, Enum):
    """订单状态"""
    PENDING = "pending"
    OPEN = "open"
    FILLED = "filled"
    CANCELLED = "cancelled"
    FAILED = "failed"


class PositionStatus(str, Enum):
    """仓位状态"""
    OPEN = "open"
    CLOSED = "closed"


class TriggerType(str, Enum):
    """触发类型"""
    ENTRY = "entry"  # 入场
    STOP_LOSS = "stop_loss"  # 止损


# ============ 市场相关模型 ============

class Market(BaseModel):
    """市场信息"""
    id: str = Field(description="市场ID")
    condition_id: str = Field(default="", description="条件ID")
    question: str = Field(description="市场问题/标题")
    slug: str = Field(default="", description="市场slug")
    
    # 价格信息
    yes_price: float = Field(default=0, description="YES价格")
    no_price: float = Field(default=0, description="NO价格")
    
    # 市场信息
    category: str = Field(default="", description="分类")
    end_date: Optional[datetime] = Field(default=None, description="比赛开始时间")
    volume: float = Field(default=0, description="交易量")
    liquidity: float = Field(default=0, description="流动性")
    
    # Token信息
    token_id: str = Field(default="", description="Token ID")
    outcome: str = Field(default="Yes", description="结果类型 Yes/No")
    
    # 计算属性
    @property
    def hours_to_end(self) -> Optional[float]:
        """距离比赛开始还有多少小时（负数表示已开始）"""
        if self.end_date:
            delta = self.end_date - datetime.utcnow()
            return delta.total_seconds() / 3600
        return None
    
    @property
    def is_sport_market(self) -> bool:
        """是否是体育市场"""
        return self.category.lower() in ['sports', 'sport', '体育']


class MarketPrice(BaseModel):
    """市场价格快照"""
    market_id: str
    token_id: str
    price: float
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    bid: float = Field(default=0, description="买一价")
    ask: float = Field(default=0, description="卖一价")
    spread: float = Field(default=0, description="价差")


# ============ 订单相关模型 ============

class Order(BaseModel):
    """订单信息"""
    id: str = Field(default="", description="订单ID")
    market_id: str = Field(description="市场ID")
    token_id: str = Field(default="", description="Token ID")
    
    side: OrderSide = Field(description="买卖方向")
    price: float = Field(description="价格")
    size: float = Field(description="数量")
    amount: float = Field(default=0, description="金额")
    
    status: OrderStatus = Field(default=OrderStatus.PENDING)
    filled_size: float = Field(default=0, description="已成交数量")
    
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    
    trigger_type: Optional[TriggerType] = Field(default=None, description="触发类型")
    error_message: Optional[str] = Field(default=None, description="错误信息")


class OrderRequest(BaseModel):
    """下单请求"""
    market_id: str
    token_id: str
    side: OrderSide
    price: float = Field(ge=0.01, le=0.99)
    amount: float = Field(ge=1, description="下单金额USDC")


# ============ 仓位相关模型 ============

class Position(BaseModel):
    """持仓信息"""
    id: str = Field(default="", description="仓位ID")
    market_id: str = Field(description="市场ID")
    token_id: str = Field(default="", description="Token ID")
    market_question: str = Field(default="", description="市场标题")
    
    # 仓位信息
    size: float = Field(default=0, description="持仓数量")
    avg_price: float = Field(default=0, description="平均成本")
    current_price: float = Field(default=0, description="当前价格")
    
    # 盈亏
    cost: float = Field(default=0, description="总成本")
    value: float = Field(default=0, description="当前价值")
    unrealized_pnl: float = Field(default=0, description="未实现盈亏")
    realized_pnl: float = Field(default=0, description="已实现盈亏")
    
    # 状态
    status: PositionStatus = Field(default=PositionStatus.OPEN)
    
    # 止损配置
    stop_loss_price: float = Field(default=0, description="止损价格")
    stop_loss_triggered: bool = Field(default=False, description="止损是否已触发")
    
    # 时间
    opened_at: datetime = Field(default_factory=datetime.utcnow)
    closed_at: Optional[datetime] = Field(default=None)
    
    def update_pnl(self, current_price: float):
        """更新盈亏"""
        self.current_price = current_price
        self.value = self.size * current_price
        self.unrealized_pnl = self.value - self.cost


# ============ 账户相关模型 ============

class Balance(BaseModel):
    """账户余额"""
    available: float = Field(default=0, description="可用余额")
    locked: float = Field(default=0, description="冻结余额")
    total: float = Field(default=0, description="总余额")
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class AccountInfo(BaseModel):
    """账户信息"""
    address: str = Field(default="", description="钱包地址")
    balance: Balance = Field(default_factory=Balance)
    positions: List[Position] = Field(default_factory=list)
    
    # 统计信息
    total_position_value: float = Field(default=0, description="总持仓价值")
    total_unrealized_pnl: float = Field(default=0, description="总未实现盈亏")
    daily_pnl: float = Field(default=0, description="当日盈亏")


# ============ 监控相关模型 ============

class MonitoredMarket(BaseModel):
    """被监控的市场"""
    market_id: str
    token_id: str
    market_question: str
    
    entry_price: float = Field(description="入场价格")
    stop_loss_price: float = Field(description="止损价格")
    current_price: float = Field(default=0)
    
    is_monitoring: bool = Field(default=True)
    has_position: bool = Field(default=False)
    position_size: float = Field(default=0)
    
    last_check: datetime = Field(default_factory=datetime.utcnow)
    created_at: datetime = Field(default_factory=datetime.utcnow)


# ============ API响应模型 ============

class ApiResponse(BaseModel):
    """统一API响应"""
    success: bool = True
    message: str = ""
    data: Optional[dict] = None


class TradingStatus(BaseModel):
    """交易状态"""
    is_running: bool = Field(default=False, description="是否运行中")
    auto_trading: bool = Field(default=False, description="自动交易是否开启")
    monitored_markets: int = Field(default=0, description="监控中的市场数")
    open_positions: int = Field(default=0, description="持仓数量")
    daily_pnl: float = Field(default=0, description="当日盈亏")
    last_scan: Optional[datetime] = Field(default=None, description="上次扫描时间")


# ============ 前端配置更新请求 ============

class TradingConfigUpdate(BaseModel):
    """交易配置更新请求"""
    entry_price: Optional[float] = Field(default=None, ge=0, le=100)
    stop_loss_price: Optional[float] = Field(default=None, ge=0, le=100)
    order_amount: Optional[float] = Field(default=None, ge=1)
    max_position_amount: Optional[float] = Field(default=None, ge=1)
    time_filter_hours: Optional[float] = Field(default=None, ge=0.1)
    scan_interval: Optional[int] = Field(default=None, ge=5)
    price_check_interval: Optional[int] = Field(default=None, ge=1)
    max_daily_loss: Optional[float] = Field(default=None, ge=0)
    max_open_positions: Optional[int] = Field(default=None, ge=1)
    auto_trading_enabled: Optional[bool] = Field(default=None)


class TelegramConfigUpdate(BaseModel):
    """Telegram配置更新请求"""
    enabled: Optional[bool] = None
    bot_token: Optional[str] = None
    chat_id: Optional[str] = None
