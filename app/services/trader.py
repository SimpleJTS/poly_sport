"""
交易服务 - 核心交易逻辑
"""

import asyncio
import uuid
from datetime import datetime
from typing import Dict, List, Optional, Set

from app.models import (
    Market, MarketPrice, Order, Position, OrderSide, OrderStatus, 
    PositionStatus, TriggerType, MonitoredMarket, TradingStatus
)
from app.config import config_manager
from app.database import db
from app.services.polymarket import polymarket_client
from app.services.telegram import telegram_notifier
from app.utils.logger import get_logger, LogMessages

logger = get_logger("trader")


class TradingService:
    """交易服务"""
    
    def __init__(self):
        self._running = False
        self._scan_task: Optional[asyncio.Task] = None
        self._monitor_task: Optional[asyncio.Task] = None
        
        # 监控中的市场
        self._monitored_markets: Dict[str, MonitoredMarket] = {}
        
        # 已处理的市场（避免重复入场）
        self._processed_markets: Set[str] = set()
        
        # 状态
        self._last_scan_time: Optional[datetime] = None
        self._daily_pnl: float = 0
    
    @property
    def is_running(self) -> bool:
        return self._running
    
    @property
    def status(self) -> TradingStatus:
        """获取交易状态"""
        return TradingStatus(
            is_running=self._running,
            auto_trading=config_manager.trading.auto_trading_enabled,
            monitored_markets=len(self._monitored_markets),
            open_positions=len([m for m in self._monitored_markets.values() if m.has_position]),
            daily_pnl=self._daily_pnl,
            last_scan=self._last_scan_time
        )
    
    async def start(self):
        """启动交易服务"""
        if self._running:
            logger.warning("交易服务已在运行中")
            return
        
        logger.info(LogMessages.SYSTEM_START)
        self._running = True
        
        # 启动扫描任务
        self._scan_task = asyncio.create_task(self._scan_loop())
        
        # 启动价格监控任务
        self._monitor_task = asyncio.create_task(self._monitor_loop())
        
        # 发送启动通知
        await telegram_notifier.notify_system_start()
    
    async def stop(self):
        """停止交易服务"""
        if not self._running:
            return
        
        logger.info(LogMessages.SYSTEM_STOP)
        self._running = False
        
        # 取消任务
        if self._scan_task:
            self._scan_task.cancel()
            try:
                await self._scan_task
            except asyncio.CancelledError:
                pass
        
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
        
        # 发送停止通知
        await telegram_notifier.notify_system_stop()
    
    async def _scan_loop(self):
        """市场扫描循环"""
        while self._running:
            try:
                await self._scan_markets()
                self._last_scan_time = datetime.utcnow()
                
            except Exception as e:
                logger.error(f"扫描市场错误: {e}")
            
            # 等待下次扫描
            await asyncio.sleep(config_manager.trading.scan_interval)
    
    async def _scan_markets(self):
        """扫描市场"""
        cfg = config_manager.trading
        
        logger.info(LogMessages.MARKET_SCAN_START)
        logger.info(f"入场配置: 入场价>={cfg.entry_price}, 时间过滤={cfg.time_filter_hours}小时")
        
        # 获取符合条件的市场
        markets = await polymarket_client.get_sport_markets(cfg.time_filter_hours)
        
        if not markets:
            logger.info("没有市场通过时间过滤，无需检查入场条件")
            return
        
        # 统计价格分布
        price_below = 0
        price_match = 0
        
        for market in markets:
            # 检查价格是否达到入场条件
            price = market.yes_price * 100  # 转换为0-100
            
            if price < cfg.entry_price:
                price_below += 1
                logger.debug(f"价格未达入场: {market.question[:40]}... 价格={price:.2f} < {cfg.entry_price}")
                continue
            
            price_match += 1
            
            # 检查是否已处理过
            if market.id in self._processed_markets:
                continue
            
            # 检查是否已有仓位
            if market.id in self._monitored_markets and self._monitored_markets[market.id].has_position:
                continue
            
            # 检查持仓限制
            open_positions = len([m for m in self._monitored_markets.values() if m.has_position])
            if open_positions >= cfg.max_open_positions:
                logger.warning(f"达到最大持仓数限制: {cfg.max_open_positions}")
                continue
            
            logger.info(f"发现入场信号: {market.question[:50]}... 价格: {price:.2f}")
            
            # 添加到监控
            monitored = MonitoredMarket(
                market_id=market.id,
                token_id=market.token_id,
                market_question=market.question,
                entry_price=cfg.entry_price,
                stop_loss_price=cfg.stop_loss_price,
                current_price=price
            )
            self._monitored_markets[market.id] = monitored
            
            # 如果启用自动交易，执行买入
            if cfg.auto_trading_enabled:
                await self._execute_entry(market, price)
            else:
                # 发送价格提醒
                await telegram_notifier.notify_price_alert(
                    market.question, price, "entry"
                )
        
        # 输出扫描统计
        logger.info(f"入场条件检查: 通过时间过滤的市场={len(markets)}, 价格未达标={price_below}, 价格符合={price_match}")
    
    async def _monitor_loop(self):
        """价格监控循环"""
        while self._running:
            try:
                await self._check_prices()
                
            except Exception as e:
                logger.error(f"价格监控错误: {e}")
            
            # 等待下次检查
            await asyncio.sleep(config_manager.trading.price_check_interval)
    
    async def _check_prices(self):
        """检查价格并执行止损"""
        cfg = config_manager.trading
        
        for market_id, monitored in list(self._monitored_markets.items()):
            if not monitored.is_monitoring or not monitored.has_position:
                continue
            
            try:
                # 获取当前价格
                price_data = await polymarket_client.get_market_price(monitored.token_id)
                if not price_data:
                    continue
                
                current_price = price_data.price
                monitored.current_price = current_price
                monitored.last_check = datetime.utcnow()
                
                logger.debug(LogMessages.PRICE_UPDATE.format(
                    market_id=market_id[:8], price=current_price
                ))
                
                # 检查止损
                if current_price <= monitored.stop_loss_price:
                    logger.warning(LogMessages.STOP_LOSS_TRIGGERED.format(
                        market_id=market_id[:8], price=current_price
                    ))
                    
                    if cfg.auto_trading_enabled:
                        await self._execute_stop_loss(monitored, current_price)
                    else:
                        # 发送止损提醒
                        await telegram_notifier.notify_price_alert(
                            monitored.market_question, current_price, "stop_loss"
                        )
                
            except Exception as e:
                logger.error(f"检查价格错误 {market_id[:8]}: {e}")
    
    async def _execute_entry(self, market: Market, price: float):
        """执行入场买入"""
        cfg = config_manager.trading
        
        try:
            # 检查余额
            balance = await polymarket_client.get_balance()
            if balance.available < cfg.order_amount:
                logger.error(LogMessages.BALANCE_LOW.format(
                    balance=balance.available, required=cfg.order_amount
                ))
                return
            
            # 检查是否超过最大持仓金额
            current_position_value = sum(
                m.position_size * m.current_price / 100 
                for m in self._monitored_markets.values() 
                if m.has_position
            )
            if current_position_value + cfg.order_amount > cfg.max_position_amount:
                logger.warning("超过最大持仓金额限制")
                return
            
            logger.info(LogMessages.ORDER_PLACING.format(
                market_id=market.id[:8], side="买入", amount=cfg.order_amount
            ))
            
            # 下买单
            order = await polymarket_client.place_order(
                token_id=market.token_id,
                side=OrderSide.BUY,
                price=price,
                amount=cfg.order_amount
            )
            
            if order:
                order.trigger_type = TriggerType.ENTRY
                await db.save_order(order)
                
                # 更新监控状态
                if market.id in self._monitored_markets:
                    self._monitored_markets[market.id].has_position = True
                    self._monitored_markets[market.id].position_size = order.size
                
                # 创建仓位记录
                position = Position(
                    id=str(uuid.uuid4()),
                    market_id=market.id,
                    token_id=market.token_id,
                    market_question=market.question,
                    size=order.size,
                    avg_price=price,
                    current_price=price,
                    cost=cfg.order_amount,
                    value=cfg.order_amount,
                    stop_loss_price=cfg.stop_loss_price
                )
                await db.save_position(position)
                
                # 记录交易
                await db.record_trade(
                    order.id, market.id, "BUY", price, order.size, cfg.order_amount
                )
                
                # 标记已处理
                self._processed_markets.add(market.id)
                
                # 发送通知
                await telegram_notifier.notify_buy(
                    market.question, price, cfg.order_amount, order.id
                )
                
                logger.info(LogMessages.POSITION_OPENED.format(
                    market_id=market.id[:8], quantity=order.size, cost=cfg.order_amount
                ))
            
        except Exception as e:
            logger.error(f"执行入场失败: {e}")
            await telegram_notifier.notify_error(f"入场失败: {str(e)[:100]}")
    
    async def _execute_stop_loss(self, monitored: MonitoredMarket, current_price: float):
        """执行止损卖出"""
        try:
            # 获取仓位
            position = await db.get_position_by_market(monitored.market_id)
            if not position:
                logger.warning(f"未找到仓位: {monitored.market_id[:8]}")
                return
            
            # 计算卖出金额
            sell_amount = monitored.position_size * current_price / 100
            
            logger.info(LogMessages.ORDER_PLACING.format(
                market_id=monitored.market_id[:8], side="卖出", amount=sell_amount
            ))
            
            # 下卖单
            order = await polymarket_client.place_order(
                token_id=monitored.token_id,
                side=OrderSide.SELL,
                price=current_price,
                amount=sell_amount
            )
            
            if order:
                order.trigger_type = TriggerType.STOP_LOSS
                await db.save_order(order)
                
                # 计算盈亏
                pnl = sell_amount - position.cost
                self._daily_pnl += pnl
                
                # 更新仓位
                position.status = PositionStatus.CLOSED
                position.current_price = current_price
                position.value = sell_amount
                position.realized_pnl = pnl
                position.closed_at = datetime.utcnow()
                position.stop_loss_triggered = True
                await db.save_position(position)
                
                # 记录交易
                await db.record_trade(
                    order.id, monitored.market_id, "SELL", 
                    current_price, order.size, sell_amount, pnl
                )
                
                # 更新监控状态
                monitored.has_position = False
                monitored.is_monitoring = False
                
                # 发送通知
                await telegram_notifier.notify_stop_loss(
                    monitored.market_question,
                    current_price,
                    position.avg_price,
                    abs(pnl) if pnl < 0 else -pnl
                )
                
                logger.info(LogMessages.STOP_LOSS_EXECUTED.format(
                    market_id=monitored.market_id[:8], quantity=order.size
                ))
            
        except Exception as e:
            logger.error(f"执行止损失败: {e}")
            await telegram_notifier.notify_error(f"止损失败: {str(e)[:100]}")
    
    async def manual_buy(self, market_id: str, token_id: str, price: float,
                        amount: float, market_question: str = "", market_order: bool = False) -> Optional[Order]:
        """
        手动买入

        Args:
            market_id: 市场ID
            token_id: Token ID
            price: 价格（0-100），市价订单时会被忽略
            amount: 金额（USDC）
            market_question: 市场问题描述
            market_order: 是否为市价订单（True=市价，False=限价）
        """
        cfg = config_manager.trading

        try:
            order = await polymarket_client.place_order(
                token_id=token_id,
                side=OrderSide.BUY,
                price=price,
                amount=amount,
                market_order=market_order
            )
            
            if order:
                order.trigger_type = TriggerType.ENTRY
                await db.save_order(order)
                
                # 添加到监控
                monitored = MonitoredMarket(
                    market_id=market_id,
                    token_id=token_id,
                    market_question=market_question,
                    entry_price=price,
                    stop_loss_price=cfg.stop_loss_price,
                    current_price=price,
                    has_position=True,
                    position_size=order.size
                )
                self._monitored_markets[market_id] = monitored
                
                # 创建仓位
                position = Position(
                    id=str(uuid.uuid4()),
                    market_id=market_id,
                    token_id=token_id,
                    market_question=market_question,
                    size=order.size,
                    avg_price=price,
                    current_price=price,
                    cost=amount,
                    value=amount,
                    stop_loss_price=cfg.stop_loss_price
                )
                await db.save_position(position)
                
                await db.record_trade(order.id, market_id, "BUY", price, order.size, amount)
                await telegram_notifier.notify_buy(market_question, price, amount, order.id)
                
                return order
            
        except Exception as e:
            logger.error(f"手动买入失败: {e}")
        
        return None
    
    async def manual_sell(self, market_id: str) -> Optional[Order]:
        """手动卖出"""
        try:
            # 获取监控信息
            monitored = self._monitored_markets.get(market_id)
            if not monitored:
                logger.error(f"未找到监控市场: {market_id}")
                return None
            
            # 获取仓位
            position = await db.get_position_by_market(market_id)
            if not position:
                logger.error(f"未找到仓位: {market_id}")
                return None
            
            # 获取当前价格
            price_data = await polymarket_client.get_market_price(monitored.token_id)
            current_price = price_data.price if price_data else monitored.current_price
            
            sell_amount = monitored.position_size * current_price / 100
            
            order = await polymarket_client.place_order(
                token_id=monitored.token_id,
                side=OrderSide.SELL,
                price=current_price,
                amount=sell_amount
            )
            
            if order:
                await db.save_order(order)
                
                pnl = sell_amount - position.cost
                self._daily_pnl += pnl
                
                position.status = PositionStatus.CLOSED
                position.current_price = current_price
                position.value = sell_amount
                position.realized_pnl = pnl
                position.closed_at = datetime.utcnow()
                await db.save_position(position)
                
                await db.record_trade(
                    order.id, market_id, "SELL", 
                    current_price, order.size, sell_amount, pnl
                )
                
                monitored.has_position = False
                monitored.is_monitoring = False
                
                await telegram_notifier.notify_sell(
                    monitored.market_question, current_price, sell_amount, pnl, "手动卖出"
                )
                
                return order
            
        except Exception as e:
            logger.error(f"手动卖出失败: {e}")
        
        return None
    
    def get_monitored_markets(self) -> List[MonitoredMarket]:
        """获取监控中的市场"""
        return list(self._monitored_markets.values())
    
    async def refresh_daily_pnl(self):
        """刷新当日盈亏"""
        self._daily_pnl = await db.get_daily_pnl()


# 全局交易服务实例
trading_service = TradingService()
