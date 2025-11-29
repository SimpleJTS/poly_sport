"""
数据库模块 - SQLite异步操作
"""

import aiosqlite
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional
import json

from app.models import Order, Position, OrderStatus, PositionStatus, OrderSide


class Database:
    """SQLite数据库管理"""
    
    def __init__(self, db_path: str = "data/trading.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection: Optional[aiosqlite.Connection] = None
    
    async def connect(self):
        """连接数据库"""
        self._connection = await aiosqlite.connect(str(self.db_path))
        self._connection.row_factory = aiosqlite.Row
        await self._create_tables()
    
    async def disconnect(self):
        """断开连接"""
        if self._connection:
            await self._connection.close()
            self._connection = None
    
    async def _create_tables(self):
        """创建数据表"""
        await self._connection.executescript("""
            -- 订单表
            CREATE TABLE IF NOT EXISTS orders (
                id TEXT PRIMARY KEY,
                market_id TEXT NOT NULL,
                token_id TEXT,
                side TEXT NOT NULL,
                price REAL NOT NULL,
                size REAL NOT NULL,
                amount REAL DEFAULT 0,
                status TEXT DEFAULT 'pending',
                filled_size REAL DEFAULT 0,
                trigger_type TEXT,
                error_message TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            
            -- 仓位表
            CREATE TABLE IF NOT EXISTS positions (
                id TEXT PRIMARY KEY,
                market_id TEXT NOT NULL,
                token_id TEXT,
                market_question TEXT,
                size REAL DEFAULT 0,
                avg_price REAL DEFAULT 0,
                current_price REAL DEFAULT 0,
                cost REAL DEFAULT 0,
                value REAL DEFAULT 0,
                unrealized_pnl REAL DEFAULT 0,
                realized_pnl REAL DEFAULT 0,
                status TEXT DEFAULT 'open',
                stop_loss_price REAL DEFAULT 0,
                stop_loss_triggered INTEGER DEFAULT 0,
                opened_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                closed_at TIMESTAMP
            );
            
            -- 交易记录表
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id TEXT,
                market_id TEXT NOT NULL,
                side TEXT NOT NULL,
                price REAL NOT NULL,
                size REAL NOT NULL,
                amount REAL NOT NULL,
                pnl REAL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            
            -- 每日统计表
            CREATE TABLE IF NOT EXISTS daily_stats (
                date TEXT PRIMARY KEY,
                total_trades INTEGER DEFAULT 0,
                total_volume REAL DEFAULT 0,
                realized_pnl REAL DEFAULT 0,
                win_trades INTEGER DEFAULT 0,
                loss_trades INTEGER DEFAULT 0
            );
            
            -- 创建索引
            CREATE INDEX IF NOT EXISTS idx_orders_market ON orders(market_id);
            CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
            CREATE INDEX IF NOT EXISTS idx_positions_market ON positions(market_id);
            CREATE INDEX IF NOT EXISTS idx_positions_status ON positions(status);
            CREATE INDEX IF NOT EXISTS idx_trades_date ON trades(created_at);
        """)
        await self._connection.commit()
    
    # ============ 订单操作 ============
    
    async def save_order(self, order: Order):
        """保存订单"""
        await self._connection.execute("""
            INSERT OR REPLACE INTO orders 
            (id, market_id, token_id, side, price, size, amount, status, 
             filled_size, trigger_type, error_message, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            order.id, order.market_id, order.token_id, order.side.value,
            order.price, order.size, order.amount, order.status.value,
            order.filled_size, order.trigger_type.value if order.trigger_type else None,
            order.error_message, order.created_at, order.updated_at
        ))
        await self._connection.commit()
    
    async def get_order(self, order_id: str) -> Optional[Order]:
        """获取订单"""
        cursor = await self._connection.execute(
            "SELECT * FROM orders WHERE id = ?", (order_id,)
        )
        row = await cursor.fetchone()
        if row:
            return self._row_to_order(row)
        return None
    
    async def get_orders_by_status(self, status: OrderStatus) -> List[Order]:
        """按状态获取订单"""
        cursor = await self._connection.execute(
            "SELECT * FROM orders WHERE status = ? ORDER BY created_at DESC",
            (status.value,)
        )
        rows = await cursor.fetchall()
        return [self._row_to_order(row) for row in rows]
    
    async def get_recent_orders(self, limit: int = 50) -> List[Order]:
        """获取最近订单"""
        cursor = await self._connection.execute(
            "SELECT * FROM orders ORDER BY created_at DESC LIMIT ?",
            (limit,)
        )
        rows = await cursor.fetchall()
        return [self._row_to_order(row) for row in rows]
    
    def _row_to_order(self, row) -> Order:
        """将数据库行转换为Order对象"""
        from app.models import TriggerType
        return Order(
            id=row['id'],
            market_id=row['market_id'],
            token_id=row['token_id'] or "",
            side=OrderSide(row['side']),
            price=row['price'],
            size=row['size'],
            amount=row['amount'] or 0,
            status=OrderStatus(row['status']),
            filled_size=row['filled_size'] or 0,
            trigger_type=TriggerType(row['trigger_type']) if row['trigger_type'] else None,
            error_message=row['error_message'],
            created_at=datetime.fromisoformat(row['created_at']) if row['created_at'] else datetime.utcnow(),
            updated_at=datetime.fromisoformat(row['updated_at']) if row['updated_at'] else datetime.utcnow()
        )
    
    # ============ 仓位操作 ============
    
    async def save_position(self, position: Position):
        """保存仓位"""
        await self._connection.execute("""
            INSERT OR REPLACE INTO positions 
            (id, market_id, token_id, market_question, size, avg_price, current_price,
             cost, value, unrealized_pnl, realized_pnl, status, stop_loss_price,
             stop_loss_triggered, opened_at, closed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            position.id, position.market_id, position.token_id, position.market_question,
            position.size, position.avg_price, position.current_price,
            position.cost, position.value, position.unrealized_pnl, position.realized_pnl,
            position.status.value, position.stop_loss_price,
            1 if position.stop_loss_triggered else 0,
            position.opened_at, position.closed_at
        ))
        await self._connection.commit()
    
    async def get_position(self, position_id: str) -> Optional[Position]:
        """获取仓位"""
        cursor = await self._connection.execute(
            "SELECT * FROM positions WHERE id = ?", (position_id,)
        )
        row = await cursor.fetchone()
        if row:
            return self._row_to_position(row)
        return None
    
    async def get_position_by_market(self, market_id: str) -> Optional[Position]:
        """按市场获取仓位"""
        cursor = await self._connection.execute(
            "SELECT * FROM positions WHERE market_id = ? AND status = 'open'",
            (market_id,)
        )
        row = await cursor.fetchone()
        if row:
            return self._row_to_position(row)
        return None
    
    async def get_open_positions(self) -> List[Position]:
        """获取所有开放仓位"""
        cursor = await self._connection.execute(
            "SELECT * FROM positions WHERE status = 'open' ORDER BY opened_at DESC"
        )
        rows = await cursor.fetchall()
        return [self._row_to_position(row) for row in rows]
    
    async def get_all_positions(self, limit: int = 100) -> List[Position]:
        """获取所有仓位"""
        cursor = await self._connection.execute(
            "SELECT * FROM positions ORDER BY opened_at DESC LIMIT ?",
            (limit,)
        )
        rows = await cursor.fetchall()
        return [self._row_to_position(row) for row in rows]
    
    def _row_to_position(self, row) -> Position:
        """将数据库行转换为Position对象"""
        return Position(
            id=row['id'],
            market_id=row['market_id'],
            token_id=row['token_id'] or "",
            market_question=row['market_question'] or "",
            size=row['size'] or 0,
            avg_price=row['avg_price'] or 0,
            current_price=row['current_price'] or 0,
            cost=row['cost'] or 0,
            value=row['value'] or 0,
            unrealized_pnl=row['unrealized_pnl'] or 0,
            realized_pnl=row['realized_pnl'] or 0,
            status=PositionStatus(row['status']),
            stop_loss_price=row['stop_loss_price'] or 0,
            stop_loss_triggered=bool(row['stop_loss_triggered']),
            opened_at=datetime.fromisoformat(row['opened_at']) if row['opened_at'] else datetime.utcnow(),
            closed_at=datetime.fromisoformat(row['closed_at']) if row['closed_at'] else None
        )
    
    # ============ 统计操作 ============
    
    async def record_trade(self, order_id: str, market_id: str, side: str,
                          price: float, size: float, amount: float, pnl: float = 0):
        """记录交易"""
        await self._connection.execute("""
            INSERT INTO trades (order_id, market_id, side, price, size, amount, pnl)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (order_id, market_id, side, price, size, amount, pnl))
        await self._connection.commit()
    
    async def get_daily_pnl(self, date: str = None) -> float:
        """获取当日盈亏"""
        if date is None:
            date = datetime.utcnow().strftime("%Y-%m-%d")
        
        cursor = await self._connection.execute("""
            SELECT COALESCE(SUM(pnl), 0) as total_pnl FROM trades
            WHERE DATE(created_at) = ?
        """, (date,))
        row = await cursor.fetchone()
        return row['total_pnl'] if row else 0
    
    async def get_daily_stats(self, date: str = None) -> dict:
        """获取当日统计"""
        if date is None:
            date = datetime.utcnow().strftime("%Y-%m-%d")
        
        cursor = await self._connection.execute("""
            SELECT 
                COUNT(*) as total_trades,
                COALESCE(SUM(amount), 0) as total_volume,
                COALESCE(SUM(pnl), 0) as realized_pnl,
                SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as win_trades,
                SUM(CASE WHEN pnl < 0 THEN 1 ELSE 0 END) as loss_trades
            FROM trades
            WHERE DATE(created_at) = ?
        """, (date,))
        row = await cursor.fetchone()
        
        return {
            'date': date,
            'total_trades': row['total_trades'] or 0,
            'total_volume': row['total_volume'] or 0,
            'realized_pnl': row['realized_pnl'] or 0,
            'win_trades': row['win_trades'] or 0,
            'loss_trades': row['loss_trades'] or 0
        }


# 全局数据库实例
db = Database()
