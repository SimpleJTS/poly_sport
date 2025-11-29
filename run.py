#!/usr/bin/env python3
"""
启动脚本
"""

import uvicorn
from app.config import config_manager

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host=config_manager.app.host,
        port=config_manager.app.port,
        reload=config_manager.app.debug
    )
