"""
Polymarket Sport Market Trading Bot
主应用入口
"""

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.config import config_manager
from app.database import db
from app.services.polymarket import polymarket_client
from app.services.telegram import telegram_notifier
from app.services.trader import trading_service
from app.utils.logger import setup_logger, LogMessages

# 初始化日志
logger = setup_logger("polymarket_trader", config_manager.app.log_dir)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # 启动时
    logger.info(LogMessages.SYSTEM_START)
    logger.info(LogMessages.CONFIG_LOADED)
    
    # 连接数据库
    await db.connect()
    logger.info("数据库连接成功")
    
    # 初始化Polymarket客户端
    await polymarket_client.initialize()
    if polymarket_client.is_initialized:
        logger.info(f"Polymarket客户端初始化成功，钱包地址: {polymarket_client.wallet_address}")
    else:
        logger.warning("Polymarket客户端未完全初始化（缺少私钥）")
    
    # 初始化Telegram
    await telegram_notifier.initialize()
    if telegram_notifier.is_configured:
        logger.info("Telegram通知已配置")
    
    # 刷新每日盈亏
    await trading_service.refresh_daily_pnl()
    
    yield
    
    # 关闭时
    logger.info(LogMessages.SYSTEM_STOP)
    
    # 停止交易服务
    await trading_service.stop()
    
    # 关闭连接
    await polymarket_client.close()
    await telegram_notifier.close()
    await db.disconnect()


# 创建应用
app = FastAPI(
    title="Polymarket尾盘交易策略",
    description="自动监控Sport市场价格并执行买入/止损的交易机器人",
    version="1.0.0",
    lifespan=lifespan
)

# CORS中间件
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册API路由
app.include_router(router, prefix="/api")

# 前端静态文件
frontend_path = Path(__file__).parent.parent / "frontend"
if frontend_path.exists():
    app.mount("/static", StaticFiles(directory=str(frontend_path)), name="static")


@app.get("/")
async def root():
    """返回前端页面"""
    index_file = frontend_path / "index.html"
    if index_file.exists():
        return FileResponse(str(index_file))
    return {"message": "Polymarket Trading Bot API", "docs": "/docs"}


@app.get("/health")
async def health_check():
    """健康检查"""
    return {
        "status": "healthy",
        "polymarket_connected": polymarket_client.is_initialized,
        "telegram_configured": telegram_notifier.is_configured,
        "trading_running": trading_service.is_running
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host=config_manager.app.host,
        port=config_manager.app.port,
        reload=config_manager.app.debug
    )
