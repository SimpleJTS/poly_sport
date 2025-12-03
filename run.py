#!/usr/bin/env python3
"""
启动脚本
"""

import uvicorn
from app.config import config_manager

if __name__ == "__main__":
    # 禁用 Uvicorn 的访问日志
    import logging
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").propagate = False
    
    uvicorn.run(
        "app.main:app",
        host=config_manager.app.host,
        port=config_manager.app.port,
        reload=config_manager.app.debug,
        access_log=False  # 禁用访问日志
    )
