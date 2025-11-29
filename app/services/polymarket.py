"""
Polymarket API客户端
封装CLOB API和Gamma API的调用
"""

import httpx
import asyncio
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any
import hmac
import hashlib
import base64
import time
import json

from eth_account import Account
from eth_account.messages import encode_defunct

from app.models import Market, MarketPrice, Order, OrderSide, OrderStatus, Balance, Position
from app.config import config_manager
from app.utils.logger import get_logger, LogMessages

logger = get_logger("polymarket")


class PolymarketClient:
    """Polymarket API客户端"""
    
    # CLOB API端点
    CLOB_HOST = "https://clob.polymarket.com"
    # Gamma API端点 (用于获取市场信息)
    GAMMA_HOST = "https://gamma-api.polymarket.com"
    
    # Chain ID
    CHAIN_ID = 137  # Polygon mainnet
    
    def __init__(self):
        self.config = config_manager.polymarket
        self._http_client: Optional[httpx.AsyncClient] = None
        self._api_creds: Optional[Dict] = None
        self._account: Optional[Account] = None
    
    async def initialize(self):
        """初始化客户端"""
        self._http_client = httpx.AsyncClient(timeout=30.0)
        
        # 初始化账户
        if self.config.private_key:
            self._account = Account.from_key(self.config.private_key)
            logger.info(f"钱包地址: {self._account.address}")
            
            # 获取API凭证
            await self._derive_api_credentials()
    
    async def close(self):
        """关闭客户端"""
        if self._http_client:
            await self._http_client.aclose()
    
    async def _derive_api_credentials(self):
        """派生API凭证"""
        if not self._account:
            logger.error("派生API凭证失败: 账户未初始化")
            return
        
        try:
            # 创建CLOB API密钥
            nonce = int(time.time() * 1000)
            timestamp = int(time.time())
            
            # 签名消息 - 使用 Polymarket 官方格式
            message = f"I want to create a new API key on Polymarket CLOB with nonce {nonce}"
            message_hash = encode_defunct(text=message)
            signed = self._account.sign_message(message_hash)
            
            # 签名需要加上 0x 前缀
            signature = "0x" + signed.signature.hex()
            
            request_body = {
                "message": message,
                "signature": signature,
                "nonce": nonce,
                "timestamp": timestamp
            }
            
            logger.debug(f"派生API凭证请求参数:")
            logger.debug(f"  钱包地址: {self._account.address}")
            logger.debug(f"  消息: {message}")
            logger.debug(f"  签名: {signature[:20]}...{signature[-10:]}")
            logger.debug(f"  nonce: {nonce}")
            logger.debug(f"  timestamp: {timestamp}")
            
            # 注册API密钥
            url = f"{self.CLOB_HOST}/auth/derive-api-key"
            logger.debug(f"  请求URL: {url}")
            
            response = await self._http_client.post(url, json=request_body)
            
            logger.debug(f"API凭证响应状态码: {response.status_code}")
            logger.debug(f"API凭证响应内容: {response.text[:500] if response.text else '(空)'}")
            
            if response.status_code == 200:
                data = response.json()
                self._api_creds = {
                    "api_key": data.get("apiKey"),
                    "api_secret": data.get("secret"),
                    "api_passphrase": data.get("passphrase")
                }
                logger.info("API凭证获取成功")
                logger.debug(f"  api_key: {self._api_creds['api_key'][:10]}..." if self._api_creds['api_key'] else "无")
            else:
                logger.error(f"获取API凭证失败:")
                logger.error(f"  状态码: {response.status_code}")
                logger.error(f"  响应: {response.text}")
                logger.error(f"  请尝试使用以下curl命令手动测试:")
                logger.error(f"  curl -X POST {url} -H 'Content-Type: application/json' -d '{json.dumps(request_body)}'")
                
        except Exception as e:
            import traceback
            logger.error(f"派生API凭证错误: {e}")
            logger.error(f"详细错误: {traceback.format_exc()}")
    
    def _get_auth_headers(self, method: str, path: str, body: str = "") -> Dict[str, str]:
        """生成认证头"""
        if not self._api_creds:
            return {}
        
        timestamp = str(int(time.time()))
        
        # 创建签名
        message = f"{timestamp}{method}{path}{body}"
        signature = hmac.new(
            base64.b64decode(self._api_creds["api_secret"]),
            message.encode(),
            hashlib.sha256
        ).digest()
        signature_b64 = base64.b64encode(signature).decode()
        
        return {
            "POLY_ADDRESS": self._account.address if self._account else "",
            "POLY_SIGNATURE": signature_b64,
            "POLY_TIMESTAMP": timestamp,
            "POLY_API_KEY": self._api_creds["api_key"],
            "POLY_PASSPHRASE": self._api_creds["api_passphrase"]
        }
    
    # ============ 市场相关 ============
    
    async def get_sport_markets(self, hours_filter: float = 1.0) -> List[Market]:
        """
        获取Sport市场列表
        
        Args:
            hours_filter: 时间过滤（距离结算时间小于此值的市场）
        
        Returns:
            符合条件的市场列表
        """
        try:
            # 使用Gamma API获取市场
            response = await self._http_client.get(
                f"{self.GAMMA_HOST}/markets",
                params={
                    "closed": "false",
                    "active": "true",
                    "limit": 500
                }
            )
            
            if response.status_code != 200:
                logger.error(f"获取市场列表失败: {response.text}")
                return []
            
            markets_data = response.json()
            markets = []
            
            now = datetime.utcnow()
            filter_threshold = now + timedelta(hours=hours_filter)
            
            for m in markets_data:
                # 检查是否是sport市场
                tags = m.get("tags", [])
                category = m.get("category", "").lower()
                
                is_sport = (
                    "sports" in [t.lower() for t in tags] or
                    "sport" in category or
                    "sports" in category or
                    any(sport in category for sport in ['nba', 'nfl', 'mlb', 'nhl', 'soccer', 'football', 'basketball', 'baseball'])
                )
                
                if not is_sport:
                    continue
                
                # 解析结束时间
                end_date_str = m.get("endDate") or m.get("end_date_iso")
                end_date = None
                if end_date_str:
                    try:
                        end_date = datetime.fromisoformat(end_date_str.replace("Z", "+00:00")).replace(tzinfo=None)
                    except:
                        pass
                
                # 时间过滤：只保留即将结算的市场
                if end_date and end_date > filter_threshold:
                    continue
                
                # 获取token信息
                tokens = m.get("tokens", [])
                if not tokens:
                    continue
                
                # 通常取YES token
                yes_token = next((t for t in tokens if t.get("outcome") == "Yes"), tokens[0])
                
                market = Market(
                    id=m.get("conditionId") or m.get("condition_id", ""),
                    condition_id=m.get("conditionId") or m.get("condition_id", ""),
                    question=m.get("question", ""),
                    slug=m.get("slug", ""),
                    yes_price=float(yes_token.get("price", 0) or 0),
                    no_price=1 - float(yes_token.get("price", 0) or 0),
                    category=category,
                    end_date=end_date,
                    volume=float(m.get("volume", 0) or 0),
                    liquidity=float(m.get("liquidity", 0) or 0),
                    token_id=yes_token.get("token_id", ""),
                    outcome="Yes"
                )
                
                markets.append(market)
            
            logger.info(LogMessages.MARKET_SCAN_COMPLETE.format(count=len(markets)))
            return markets
            
        except Exception as e:
            logger.error(LogMessages.API_ERROR.format(error=str(e)))
            return []
    
    async def get_market_price(self, token_id: str) -> Optional[MarketPrice]:
        """获取市场价格"""
        try:
            response = await self._http_client.get(
                f"{self.CLOB_HOST}/book",
                params={"token_id": token_id}
            )
            
            if response.status_code != 200:
                return None
            
            data = response.json()
            
            # 解析订单簿获取价格
            bids = data.get("bids", [])
            asks = data.get("asks", [])
            
            best_bid = float(bids[0]["price"]) if bids else 0
            best_ask = float(asks[0]["price"]) if asks else 0
            mid_price = (best_bid + best_ask) / 2 if best_bid and best_ask else best_bid or best_ask
            
            return MarketPrice(
                market_id=data.get("market", ""),
                token_id=token_id,
                price=mid_price * 100,  # 转换为0-100
                bid=best_bid * 100,
                ask=best_ask * 100,
                spread=(best_ask - best_bid) * 100 if best_ask and best_bid else 0
            )
            
        except Exception as e:
            logger.error(f"获取市场价格失败: {e}")
            return None
    
    async def get_markets_by_price(self, min_price: float = 85, max_price: float = 95, 
                                   hours_filter: float = 1.0) -> List[Market]:
        """
        获取价格在指定范围内的Sport市场
        
        Args:
            min_price: 最低价格（0-100）
            max_price: 最高价格（0-100）
            hours_filter: 时间过滤
        
        Returns:
            符合条件的市场列表
        """
        markets = await self.get_sport_markets(hours_filter)
        
        filtered = []
        for market in markets:
            price = market.yes_price * 100  # 转换为0-100
            if min_price <= price <= max_price:
                filtered.append(market)
                logger.info(f"发现符合条件市场: {market.question[:50]}... 价格: {price:.2f}")
        
        return filtered
    
    # ============ 交易相关 ============
    
    async def place_order(self, token_id: str, side: OrderSide, price: float, 
                         amount: float) -> Optional[Order]:
        """
        下单
        
        Args:
            token_id: Token ID
            side: 买卖方向
            price: 价格（0-100）
            amount: 金额（USDC）
        
        Returns:
            订单对象
        """
        if not self._api_creds or not self._account:
            logger.error("未初始化API凭证，无法下单")
            return None
        
        try:
            # 将价格转换为0-1范围
            price_decimal = price / 100
            
            # 计算数量
            size = amount / price_decimal
            
            # 构建订单
            order_data = {
                "tokenID": token_id,
                "price": str(price_decimal),
                "size": str(size),
                "side": side.value,
                "type": "GTC"  # Good Till Cancel
            }
            
            body = json.dumps(order_data)
            path = "/order"
            headers = self._get_auth_headers("POST", path, body)
            headers["Content-Type"] = "application/json"
            
            response = await self._http_client.post(
                f"{self.CLOB_HOST}{path}",
                content=body,
                headers=headers
            )
            
            if response.status_code in [200, 201]:
                data = response.json()
                order = Order(
                    id=data.get("orderID", data.get("id", "")),
                    market_id=data.get("market", ""),
                    token_id=token_id,
                    side=side,
                    price=price,
                    size=size,
                    amount=amount,
                    status=OrderStatus.OPEN
                )
                logger.info(LogMessages.ORDER_SUCCESS.format(
                    order_id=order.id, market_id=order.market_id
                ))
                return order
            else:
                logger.error(LogMessages.ORDER_FAILED.format(
                    market_id="", reason=response.text
                ))
                return None
                
        except Exception as e:
            logger.error(LogMessages.ORDER_FAILED.format(market_id="", reason=str(e)))
            return None
    
    async def cancel_order(self, order_id: str) -> bool:
        """取消订单"""
        if not self._api_creds:
            return False
        
        try:
            path = f"/order/{order_id}"
            headers = self._get_auth_headers("DELETE", path)
            
            response = await self._http_client.delete(
                f"{self.CLOB_HOST}{path}",
                headers=headers
            )
            
            if response.status_code == 200:
                logger.info(LogMessages.ORDER_CANCELLED.format(order_id=order_id))
                return True
            return False
            
        except Exception as e:
            logger.error(f"取消订单失败: {e}")
            return False
    
    async def get_open_orders(self) -> List[Dict]:
        """获取挂单"""
        if not self._api_creds or not self._account:
            return []
        
        try:
            path = "/orders"
            headers = self._get_auth_headers("GET", path)
            
            response = await self._http_client.get(
                f"{self.CLOB_HOST}{path}",
                params={"owner": self._account.address},
                headers=headers
            )
            
            if response.status_code == 200:
                return response.json()
            return []
            
        except Exception as e:
            logger.error(f"获取挂单失败: {e}")
            return []
    
    # ============ 账户相关 ============
    
    async def get_balance(self) -> Balance:
        """获取账户余额（代理钱包余额）"""
        if not self._account:
            logger.error("获取余额失败: 账户未初始化")
            return Balance()
        
        try:
            # 方法1: 尝试使用认证接口获取代理钱包余额
            if self._api_creds:
                path = "/balance"
                headers = self._get_auth_headers("GET", path)
                
                logger.debug(f"使用认证接口获取余额...")
                logger.debug(f"  地址: {self._account.address}")
                
                response = await self._http_client.get(
                    f"{self.CLOB_HOST}{path}",
                    params={"address": self._account.address},
                    headers=headers
                )
                
                logger.debug(f"余额响应状态码: {response.status_code}")
                logger.debug(f"余额响应内容: {response.text[:500] if response.text else '(空)'}")
                
                if response.status_code == 200:
                    data = response.json()
                    available = float(data.get("available", 0))
                    locked = float(data.get("locked", 0))
                    logger.info(f"代理钱包余额: 可用={available}, 锁定={locked}")
                    return Balance(
                        available=available,
                        locked=locked,
                        total=available + locked
                    )
                else:
                    logger.error(f"获取余额失败:")
                    logger.error(f"  状态码: {response.status_code}")
                    logger.error(f"  响应: {response.text}")
            else:
                logger.error("获取余额失败: API凭证未初始化")
                logger.error("请先确保API凭证获取成功")
            
            return Balance()
            
        except Exception as e:
            import traceback
            logger.error(f"获取余额失败: {e}")
            logger.error(f"详细错误: {traceback.format_exc()}")
            return Balance()
    
    async def debug_api_status(self) -> Dict[str, Any]:
        """获取API调试信息"""
        status = {
            "wallet_initialized": self._account is not None,
            "wallet_address": self._account.address if self._account else None,
            "api_creds_initialized": self._api_creds is not None,
            "http_client_initialized": self._http_client is not None,
        }
        
        if self._api_creds:
            status["api_key_preview"] = self._api_creds.get("api_key", "")[:10] + "..." if self._api_creds.get("api_key") else None
        
        return status
    
    async def get_positions(self) -> List[Position]:
        """获取持仓"""
        if not self._account:
            return []
        
        try:
            response = await self._http_client.get(
                f"{self.CLOB_HOST}/positions",
                params={"address": self._account.address}
            )
            
            if response.status_code == 200:
                data = response.json()
                positions = []
                
                for p in data:
                    size = float(p.get("size", 0))
                    if size > 0:
                        positions.append(Position(
                            id=p.get("id", ""),
                            market_id=p.get("market", ""),
                            token_id=p.get("tokenId", ""),
                            size=size,
                            avg_price=float(p.get("avgPrice", 0)) * 100,
                            current_price=float(p.get("currentPrice", 0)) * 100
                        ))
                
                return positions
            
            return []
            
        except Exception as e:
            logger.error(f"获取持仓失败: {e}")
            return []
    
    @property
    def wallet_address(self) -> str:
        """获取钱包地址"""
        return self._account.address if self._account else ""
    
    @property
    def is_initialized(self) -> bool:
        """是否已初始化"""
        return self._account is not None


# 全局客户端实例
polymarket_client = PolymarketClient()
