"""
日志工具模块
- 支持中文输出
- 按每2小时切割日志文件
"""

import os
import logging
from logging.handlers import TimedRotatingFileHandler
from datetime import datetime
from pathlib import Path


class ChineseFormatter(logging.Formatter):
    """中文日志格式化器"""
    
    LEVEL_NAMES = {
        'DEBUG': '调试',
        'INFO': '信息',
        'WARNING': '警告',
        'ERROR': '错误',
        'CRITICAL': '严重'
    }
    
    def format(self, record):
        # 转换日志级别为中文
        record.levelname_cn = self.LEVEL_NAMES.get(record.levelname, record.levelname)
        return super().format(record)


class TwoHourRotatingHandler(TimedRotatingFileHandler):
    """每2小时切割的日志处理器"""
    
    def __init__(self, filename, encoding='utf-8'):
        # 确保日志目录存在
        log_dir = Path(filename).parent
        log_dir.mkdir(parents=True, exist_ok=True)
        
        super().__init__(
            filename=filename,
            when='H',  # 按小时
            interval=2,  # 每2小时
            backupCount=168,  # 保留14天的日志 (14*24/2=168)
            encoding=encoding,
            delay=False
        )
        
        # 自定义后缀格式
        self.suffix = "%Y%m%d_%H%M"
    
    def doRollover(self):
        """重写切割方法，自定义文件名格式"""
        super().doRollover()


def setup_logger(name: str = "polymarket_trader", log_dir: str = "logs") -> logging.Logger:
    """
    设置日志记录器
    
    Args:
        name: 日志记录器名称
        log_dir: 日志目录
    
    Returns:
        配置好的Logger实例
    """
    logger = logging.getLogger(name)
    
    # 避免重复添加handler
    if logger.handlers:
        return logger
    
    logger.setLevel(logging.DEBUG)
    
    # 日志格式 - 中文
    log_format = "%(asctime)s | %(levelname_cn)s | %(name)s | %(message)s"
    date_format = "%Y-%m-%d %H:%M:%S"
    formatter = ChineseFormatter(log_format, datefmt=date_format)
    
    # 确保日志目录存在
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)
    
    # 文件处理器 - 2小时切割
    log_file = log_path / f"{name}.log"
    file_handler = TwoHourRotatingHandler(str(log_file))
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    
    # 控制台处理器
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    
    return logger


def get_logger(name: str = "polymarket_trader") -> logging.Logger:
    """获取已配置的日志记录器"""
    logger = logging.getLogger(name)
    if not logger.handlers:
        return setup_logger(name)
    return logger


# 预定义的日志消息模板（中文）
class LogMessages:
    """中文日志消息模板"""
    
    # 系统相关
    SYSTEM_START = "系统启动"
    SYSTEM_STOP = "系统停止"
    CONFIG_LOADED = "配置加载完成"
    
    # 市场相关
    MARKET_SCAN_START = "开始扫描Sport市场"
    MARKET_SCAN_COMPLETE = "市场扫描完成，发现 {count} 个符合条件的市场"
    MARKET_FILTER = "过滤市场: {reason}"
    
    # 价格监控
    PRICE_MONITOR_START = "开始监控市场: {market_id}"
    PRICE_MONITOR_STOP = "停止监控市场: {market_id}"
    PRICE_UPDATE = "价格更新 - 市场: {market_id}, 当前价格: {price}"
    PRICE_TRIGGER = "价格触发 - 市场: {market_id}, 触发价格: {price}, 类型: {trigger_type}"
    
    # 交易相关
    ORDER_PLACING = "正在下单 - 市场: {market_id}, 方向: {side}, 金额: ${amount}"
    ORDER_SUCCESS = "下单成功 - 订单ID: {order_id}, 市场: {market_id}"
    ORDER_FAILED = "下单失败 - 市场: {market_id}, 原因: {reason}"
    ORDER_CANCELLED = "订单取消 - 订单ID: {order_id}"
    
    # 止损相关
    STOP_LOSS_TRIGGERED = "止损触发 - 市场: {market_id}, 触发价格: {price}"
    STOP_LOSS_EXECUTED = "止损执行完成 - 市场: {market_id}, 卖出数量: {quantity}"
    
    # 仓位相关
    POSITION_OPENED = "开仓 - 市场: {market_id}, 数量: {quantity}, 成本: ${cost}"
    POSITION_CLOSED = "平仓 - 市场: {market_id}, 数量: {quantity}, 盈亏: ${pnl}"
    POSITION_UPDATE = "仓位更新 - 当前持仓数: {count}, 总价值: ${value}"
    
    # 余额相关
    BALANCE_UPDATE = "余额更新 - 可用: ${available}, 已用: ${used}"
    BALANCE_LOW = "余额不足警告 - 当前余额: ${balance}, 需要: ${required}"
    
    # 通知相关
    TG_SEND_SUCCESS = "Telegram消息发送成功"
    TG_SEND_FAILED = "Telegram消息发送失败: {error}"
    
    # 错误相关
    API_ERROR = "API错误: {error}"
    CONNECTION_ERROR = "连接错误: {error}"
    RECONNECTING = "正在重新连接... 尝试次数: {attempt}"
