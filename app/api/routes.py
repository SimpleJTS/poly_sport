"""
API路由
"""

from fastapi import APIRouter, HTTPException
from typing import List, Optional

from app.models import (
    ApiResponse, TradingConfigUpdate, TelegramConfigUpdate,
    TradingStatus, Position, Order, MonitoredMarket
)
from app.config import config_manager
from app.database import db
from app.services.polymarket import polymarket_client
from app.services.telegram import telegram_notifier
from app.services.trader import trading_service
from app.utils.logger import get_logger

logger = get_logger("api")

router = APIRouter()


# ============ 系统状态 ============

@router.get("/status", response_model=TradingStatus)
async def get_status():
    """获取系统状态"""
    return trading_service.status


@router.post("/start")
async def start_trading():
    """启动交易"""
    await trading_service.start()
    return ApiResponse(success=True, message="交易服务已启动")


@router.post("/stop")
async def stop_trading():
    """停止交易"""
    await trading_service.stop()
    return ApiResponse(success=True, message="交易服务已停止")


# ============ 配置管理 ============

@router.get("/config/trading")
async def get_trading_config():
    """获取交易配置"""
    return ApiResponse(
        success=True,
        data=config_manager.get_trading_config_dict()
    )


@router.put("/config/trading")
async def update_trading_config(config: TradingConfigUpdate):
    """更新交易配置"""
    update_data = config.model_dump(exclude_none=True)
    if update_data:
        config_manager.update_trading_config(**update_data)
        logger.info(f"交易配置已更新: {update_data}")
    return ApiResponse(
        success=True,
        message="配置已更新",
        data=config_manager.get_trading_config_dict()
    )


@router.get("/config/telegram")
async def get_telegram_config():
    """获取Telegram配置"""
    return ApiResponse(
        success=True,
        data=config_manager.get_telegram_config_dict()
    )


@router.put("/config/telegram")
async def update_telegram_config(config: TelegramConfigUpdate):
    """更新Telegram配置"""
    update_data = config.model_dump(exclude_none=True)
    if update_data:
        config_manager.update_telegram_config(**update_data)
        logger.info("Telegram配置已更新")
    return ApiResponse(
        success=True,
        message="配置已更新",
        data=config_manager.get_telegram_config_dict()
    )


@router.post("/config/telegram/test")
async def test_telegram():
    """测试Telegram连接"""
    success = await telegram_notifier.test_connection()
    return ApiResponse(
        success=success,
        message="测试消息已发送" if success else "发送失败，请检查配置"
    )


# ============ 账户信息 ============

@router.get("/account/balance")
async def get_balance():
    """获取账户余额"""
    balance = await polymarket_client.get_balance()
    return ApiResponse(
        success=True,
        data={
            "available": balance.available,
            "locked": balance.locked,
            "total": balance.total,
            "wallet_address": polymarket_client.wallet_address
        }
    )


@router.get("/account/positions")
async def get_positions():
    """获取当前持仓"""
    # 从API获取
    api_positions = await polymarket_client.get_positions()
    
    # 从数据库获取
    db_positions = await db.get_open_positions()
    
    # 合并数据
    positions_data = []
    for pos in db_positions:
        positions_data.append(pos.model_dump())
    
    return ApiResponse(
        success=True,
        data={
            "positions": positions_data,
            "count": len(positions_data)
        }
    )


# ============ 市场信息 ============

@router.get("/markets/sport")
async def get_sport_markets(hours: float = 1.0, min_price: float = 0, max_price: float = 100):
    """获取Sport市场列表"""
    if min_price > 0 or max_price < 100:
        markets = await polymarket_client.get_markets_by_price(
            min_price=min_price,
            max_price=max_price,
            hours_filter=hours
        )
    else:
        markets = await polymarket_client.get_sport_markets(hours)
    
    return ApiResponse(
        success=True,
        data={
            "markets": [m.model_dump() for m in markets],
            "count": len(markets)
        }
    )


@router.get("/markets/monitored")
async def get_monitored_markets():
    """获取监控中的市场"""
    markets = trading_service.get_monitored_markets()
    return ApiResponse(
        success=True,
        data={
            "markets": [m.model_dump() for m in markets],
            "count": len(markets)
        }
    )


@router.get("/markets/{token_id}/price")
async def get_market_price(token_id: str):
    """获取市场价格"""
    price = await polymarket_client.get_market_price(token_id)
    if price:
        return ApiResponse(
            success=True,
            data=price.model_dump()
        )
    raise HTTPException(status_code=404, detail="市场未找到")


# ============ 交易操作 ============

@router.post("/trade/buy")
async def manual_buy(
    market_id: str,
    token_id: str,
    price: float,
    amount: float,
    market_question: str = ""
):
    """手动买入"""
    order = await trading_service.manual_buy(
        market_id=market_id,
        token_id=token_id,
        price=price,
        amount=amount,
        market_question=market_question
    )
    
    if order:
        return ApiResponse(
            success=True,
            message="买入订单已提交",
            data=order.model_dump()
        )
    raise HTTPException(status_code=400, detail="下单失败")


@router.post("/trade/sell/{market_id}")
async def manual_sell(market_id: str):
    """手动卖出"""
    order = await trading_service.manual_sell(market_id)
    
    if order:
        return ApiResponse(
            success=True,
            message="卖出订单已提交",
            data=order.model_dump()
        )
    raise HTTPException(status_code=400, detail="卖出失败")


# ============ 订单和交易历史 ============

@router.get("/orders/recent")
async def get_recent_orders(limit: int = 50):
    """获取最近订单"""
    orders = await db.get_recent_orders(limit)
    return ApiResponse(
        success=True,
        data={
            "orders": [o.model_dump() for o in orders],
            "count": len(orders)
        }
    )


@router.get("/stats/daily")
async def get_daily_stats(date: str = None):
    """获取每日统计"""
    stats = await db.get_daily_stats(date)
    return ApiResponse(
        success=True,
        data=stats
    )


@router.get("/positions/history")
async def get_position_history(limit: int = 100):
    """获取仓位历史"""
    positions = await db.get_all_positions(limit)
    return ApiResponse(
        success=True,
        data={
            "positions": [p.model_dump() for p in positions],
            "count": len(positions)
        }
    )
