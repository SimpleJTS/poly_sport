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

from app.models import Market, MarketPrice, Order, OrderSide, OrderStatus, Balance, Position
from app.config import config_manager
from app.utils.logger import get_logger, LogMessages

logger = get_logger("polymarket")

# EIP-712 相关常量
CLOB_DOMAIN_NAME = "ClobAuthDomain"
CLOB_VERSION = "1"
MSG_TO_SIGN = "This message attests that I control the given wallet"


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
    
    def _build_eip712_domain(self) -> Dict:
        """构建 EIP-712 Domain"""
        return {
            "name": CLOB_DOMAIN_NAME,
            "version": CLOB_VERSION,
            "chainId": self.CHAIN_ID
        }
    
    def _build_clob_auth_struct(self, timestamp: int, nonce: int) -> Dict:
        """构建 ClobAuth 结构"""
        return {
            "address": self._account.address,
            "timestamp": str(timestamp),
            "nonce": nonce,
            "message": MSG_TO_SIGN
        }
    
    def _sign_clob_auth_message(self, timestamp: int, nonce: int) -> str:
        """
        使用 EIP-712 签名认证消息
        """
        from eth_account.messages import encode_typed_data
        
        # 构建完整的 EIP-712 消息结构
        full_message = {
            "types": {
                "EIP712Domain": [
                    {"name": "name", "type": "string"},
                    {"name": "version", "type": "string"},
                    {"name": "chainId", "type": "uint256"},
                ],
                "ClobAuth": [
                    {"name": "address", "type": "address"},
                    {"name": "timestamp", "type": "string"},
                    {"name": "nonce", "type": "uint256"},
                    {"name": "message", "type": "string"},
                ]
            },
            "primaryType": "ClobAuth",
            "domain": {
                "name": CLOB_DOMAIN_NAME,
                "version": CLOB_VERSION,
                "chainId": self.CHAIN_ID
            },
            "message": {
                "address": self._account.address,
                "timestamp": str(timestamp),
                "nonce": nonce,
                "message": MSG_TO_SIGN
            }
        }
        
        signable_message = encode_typed_data(full_message=full_message)
        signed = self._account.sign_message(signable_message)
        return "0x" + signed.signature.hex()
    
    def _create_level_1_headers(self, nonce: int = 0) -> Dict[str, str]:
        """创建 Level 1 认证头"""
        timestamp = int(time.time())
        signature = self._sign_clob_auth_message(timestamp, nonce)
        
        return {
            "POLY_ADDRESS": self._account.address,
            "POLY_SIGNATURE": signature,
            "POLY_TIMESTAMP": str(timestamp),
            "POLY_NONCE": str(nonce)
        }
    
    async def _derive_api_credentials(self):
        """派生API凭证"""
        if not self._account:
            return
        
        try:
            # 使用 Level 1 认证头（GET 请求）
            headers = self._create_level_1_headers(nonce=0)
            
            # 先尝试派生已存在的 API Key
            response = await self._http_client.get(
                f"{self.CLOB_HOST}/auth/derive-api-key",
                headers=headers
            )
            
            if response.status_code == 200:
                data = response.json()
                self._api_creds = {
                    "api_key": data.get("apiKey"),
                    "api_secret": data.get("secret"),
                    "api_passphrase": data.get("passphrase")
                }
                logger.info("API凭证获取成功（派生）")
                return
            
            # 如果派生失败，尝试创建新的 API Key
            logger.info("派生API凭证失败，尝试创建新凭证...")
            headers = self._create_level_1_headers(nonce=0)
            
            response = await self._http_client.post(
                f"{self.CLOB_HOST}/auth/api-key",
                headers=headers
            )
            
            if response.status_code == 200:
                data = response.json()
                self._api_creds = {
                    "api_key": data.get("apiKey"),
                    "api_secret": data.get("secret"),
                    "api_passphrase": data.get("passphrase")
                }
                logger.info("API凭证创建成功")
            else:
                logger.error(f"获取API凭证失败: {response.status_code} - {response.text}")
                
        except Exception as e:
            logger.error(f"派生API凭证错误: {e}")
            import traceback
            logger.error(traceback.format_exc())
    
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
            # 使用 Gamma API 的 events 端点，通过 tag_slug 过滤 sport 事件
            response = await self._http_client.get(
                f"{self.GAMMA_HOST}/events",
                params={
                    "closed": "false",
                    "active": "true",
                    "tag_slug": "sports",  # 直接过滤 sports 标签
                    "limit": 100
                }
            )
            
            if response.status_code != 200:
                logger.error(f"获取Sport事件列表失败: {response.text}")
                return []
            
            events_data = response.json()
            markets = []
            
            now = datetime.utcnow()
            filter_threshold = now + timedelta(hours=hours_filter)
            
            logger.info(f"获取到 {len(events_data)} 个Sport事件")
            logger.info(f"时间过滤: 当前时间={now.strftime('%Y-%m-%d %H:%M:%S')}, 阈值={filter_threshold.strftime('%Y-%m-%d %H:%M:%S')} (未来{hours_filter}小时)")
            
            # 统计被过滤的原因
            stats = {
                "total_markets": 0,
                "closed": 0,
                "no_token": 0,
                "expired": 0,
                "too_far": 0,
                "no_end_date": 0,
                "passed": 0
            }
            
            for event in events_data:
                # 获取事件中的所有市场
                event_markets = event.get("markets", [])
                event_title = event.get("title", "")
                event_tags = [t.get("label", "") for t in event.get("tags", [])]
                
                logger.debug(f"事件: {event_title}, 市场数: {len(event_markets)}, 标签: {event_tags}")
                
                for m in event_markets:
                    stats["total_markets"] += 1
                    
                    # 检查市场是否关闭
                    if m.get("closed", False):
                        stats["closed"] += 1
                        continue
                    
                    # 解析结束时间
                    end_date_str = m.get("endDate")
                    end_date = None
                    if end_date_str:
                        try:
                            end_date = datetime.fromisoformat(end_date_str.replace("Z", "+00:00")).replace(tzinfo=None)
                        except Exception as e:
                            logger.debug(f"解析日期失败: {end_date_str}, 错误: {e}")
                    
                    # 时间过滤：只保留即将结算的市场（在 hours_filter 小时内结束）
                    if end_date:
                        if end_date < now:
                            # 已过期
                            stats["expired"] += 1
                            continue
                        if end_date > filter_threshold:
                            # 还没到尾盘时间
                            stats["too_far"] += 1
                            # 输出最近的几个市场结束时间，帮助诊断
                            if stats["too_far"] <= 3:
                                time_diff = end_date - now
                                hours_until = time_diff.total_seconds() / 3600
                                logger.debug(f"市场时间过滤: {m.get('question', '')[:50]}... 结束于 {end_date.strftime('%Y-%m-%d %H:%M')} ({hours_until:.1f}小时后)")
                            continue
                    else:
                        # 没有结束日期的市场也跳过（除非特别配置）
                        stats["no_end_date"] += 1
                        continue
                    
                    # 获取 token 信息 (API 返回的是 JSON 字符串，需要解析)
                    clob_token_ids_raw = m.get("clobTokenIds", [])
                    outcome_prices_raw = m.get("outcomePrices", [])
                    outcomes_raw = m.get("outcomes", ["Yes", "No"])
                    
                    # 解析 JSON 字符串
                    if isinstance(clob_token_ids_raw, str):
                        try:
                            clob_token_ids = json.loads(clob_token_ids_raw)
                        except:
                            clob_token_ids = []
                    else:
                        clob_token_ids = clob_token_ids_raw or []
                    
                    if isinstance(outcome_prices_raw, str):
                        try:
                            outcome_prices = json.loads(outcome_prices_raw)
                        except:
                            outcome_prices = []
                    else:
                        outcome_prices = outcome_prices_raw or []
                    
                    if isinstance(outcomes_raw, str):
                        try:
                            outcomes = json.loads(outcomes_raw)
                        except:
                            outcomes = ["Yes", "No"]
                    else:
                        outcomes = outcomes_raw or ["Yes", "No"]
                    
                    if not clob_token_ids or len(clob_token_ids) < 2:
                        stats["no_token"] += 1
                        logger.debug(f"市场缺少 token 信息: {m.get('question', '')[:50]}")
                        continue
                    
                    stats["passed"] += 1
                    
                    # 解析价格
                    yes_price = 0.0
                    no_price = 0.0
                    
                    if outcome_prices and len(outcome_prices) >= 2:
                        try:
                            yes_price = float(outcome_prices[0] or 0)
                            no_price = float(outcome_prices[1] or 0)
                        except (ValueError, TypeError):
                            pass
                    
                    # 如果没有 outcomePrices，尝试从其他字段获取
                    if yes_price == 0:
                        yes_price = float(m.get("bestAsk", 0) or m.get("lastTradePrice", 0) or 0)
                        no_price = 1 - yes_price if yes_price > 0 else 0
                    
                    # 获取 YES token ID（第一个通常是 Yes）
                    yes_token_id = clob_token_ids[0]
                    
                    condition_id = m.get("conditionId", "")
                    
                    # 构建类别字符串
                    category = ", ".join(event_tags) if event_tags else "Sports"
                    
                    market = Market(
                        id=condition_id or str(m.get("id", "")),
                        condition_id=condition_id,
                        question=m.get("question", ""),
                        slug=m.get("slug", ""),
                        yes_price=yes_price,
                        no_price=no_price,
                        category=category,
                        end_date=end_date,
                        volume=float(m.get("volume", 0) or 0),
                        liquidity=float(m.get("liquidity", 0) or 0),
                        token_id=yes_token_id,
                        outcome=outcomes[0] if outcomes else "Yes"
                    )
                    
                    markets.append(market)
                    logger.debug(f"添加市场: {market.question[:50]}... 价格: {yes_price:.4f}")
            
            # 输出过滤统计
            logger.info(f"市场过滤统计: 总计={stats['total_markets']}, 已关闭={stats['closed']}, "
                       f"已过期={stats['expired']}, 时间过远={stats['too_far']}, "
                       f"无结束时间={stats['no_end_date']}, 无Token={stats['no_token']}, 通过={stats['passed']}")
            
            if stats['too_far'] > 0 and len(markets) == 0:
                logger.warning(f"⚠️ 没有市场通过时间过滤！当前设置只查看未来{hours_filter}小时内结束的市场。"
                              f"建议增大 time_filter_hours 参数或使用 all_markets=True 查看所有市场。")
            
            logger.info(LogMessages.MARKET_SCAN_COMPLETE.format(count=len(markets)))
            return markets
            
        except Exception as e:
            logger.error(LogMessages.API_ERROR.format(error=str(e)))
            import traceback
            logger.error(traceback.format_exc())
            return []
    
    async def get_all_sport_markets(self, limit: int = 100) -> List[Market]:
        """
        获取所有Sport市场（不做时间过滤）
        用于浏览和调试
        
        Args:
            limit: 返回的最大事件数
        
        Returns:
            所有 sport 市场列表
        """
        try:
            response = await self._http_client.get(
                f"{self.GAMMA_HOST}/events",
                params={
                    "closed": "false",
                    "active": "true",
                    "tag_slug": "sports",
                    "limit": limit
                }
            )
            
            if response.status_code != 200:
                logger.error(f"获取Sport事件列表失败: {response.text}")
                return []
            
            events_data = response.json()
            markets = []
            
            for event in events_data:
                event_markets = event.get("markets", [])
                event_tags = [t.get("label", "") for t in event.get("tags", [])]
                
                for m in event_markets:
                    if m.get("closed", False):
                        continue
                    
                    # 解析结束时间
                    end_date_str = m.get("endDate")
                    end_date = None
                    if end_date_str:
                        try:
                            end_date = datetime.fromisoformat(end_date_str.replace("Z", "+00:00")).replace(tzinfo=None)
                        except:
                            pass
                    
                    # 获取 token 信息 (API 返回的是 JSON 字符串，需要解析)
                    clob_token_ids_raw = m.get("clobTokenIds", [])
                    outcome_prices_raw = m.get("outcomePrices", [])
                    outcomes_raw = m.get("outcomes", ["Yes", "No"])
                    
                    # 解析 JSON 字符串
                    if isinstance(clob_token_ids_raw, str):
                        try:
                            clob_token_ids = json.loads(clob_token_ids_raw)
                        except:
                            clob_token_ids = []
                    else:
                        clob_token_ids = clob_token_ids_raw or []
                    
                    if isinstance(outcome_prices_raw, str):
                        try:
                            outcome_prices = json.loads(outcome_prices_raw)
                        except:
                            outcome_prices = []
                    else:
                        outcome_prices = outcome_prices_raw or []
                    
                    if isinstance(outcomes_raw, str):
                        try:
                            outcomes = json.loads(outcomes_raw)
                        except:
                            outcomes = ["Yes", "No"]
                    else:
                        outcomes = outcomes_raw or ["Yes", "No"]
                    
                    if not clob_token_ids or len(clob_token_ids) < 2:
                        continue
                    
                    # 解析价格
                    yes_price = 0.0
                    no_price = 0.0
                    
                    if outcome_prices and len(outcome_prices) >= 2:
                        try:
                            yes_price = float(outcome_prices[0] or 0)
                            no_price = float(outcome_prices[1] or 0)
                        except (ValueError, TypeError):
                            pass
                    
                    if yes_price == 0:
                        yes_price = float(m.get("bestAsk", 0) or m.get("lastTradePrice", 0) or 0)
                        no_price = 1 - yes_price if yes_price > 0 else 0
                    
                    yes_token_id = clob_token_ids[0]
                    condition_id = m.get("conditionId", "")
                    category = ", ".join(event_tags) if event_tags else "Sports"
                    
                    market = Market(
                        id=condition_id or str(m.get("id", "")),
                        condition_id=condition_id,
                        question=m.get("question", ""),
                        slug=m.get("slug", ""),
                        yes_price=yes_price,
                        no_price=no_price,
                        category=category,
                        end_date=end_date,
                        volume=float(m.get("volume", 0) or 0),
                        liquidity=float(m.get("liquidity", 0) or 0),
                        token_id=yes_token_id,
                        outcome=outcomes[0] if outcomes else "Yes"
                    )
                    
                    markets.append(market)
            
            logger.info(f"获取到 {len(markets)} 个Sport市场（不含时间过滤）")
            return markets
            
        except Exception as e:
            logger.error(f"获取Sport市场失败: {e}")
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
        """获取账户余额"""
        if not self._account:
            return Balance()
        
        try:
            # 获取USDC余额
            # Polymarket使用Polygon上的USDC
            response = await self._http_client.get(
                f"{self.CLOB_HOST}/balance",
                params={"address": self._account.address}
            )
            
            if response.status_code == 200:
                data = response.json()
                available = float(data.get("available", 0))
                locked = float(data.get("locked", 0))
                return Balance(
                    available=available,
                    locked=locked,
                    total=available + locked
                )
            
            return Balance()
            
        except Exception as e:
            logger.error(f"获取余额失败: {e}")
            return Balance()
    
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
