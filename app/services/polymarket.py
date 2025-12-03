"""
Polymarket API客户端
封装CLOB API和Gamma API的调用
使用 py_clob_client 处理 CLOB API，保留 Gamma API 的自定义实现
"""

import httpx
import asyncio
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any
import json

from eth_account import Account
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, ApiCreds, BalanceAllowanceParams, AssetType, MarketOrderArgs, OrderType

from app.models import Market, MarketPrice, Order, OrderSide, OrderStatus, Balance, Position
from app.config import config_manager
from app.utils.logger import get_logger, LogMessages

logger = get_logger("polymarket")


class PolymarketClient:
    """Polymarket API客户端
    
    使用 py_clob_client 处理 CLOB API（交易相关）
    保留自定义实现处理 Gamma API（市场数据）
    """
    
    # CLOB API端点
    CLOB_HOST = "https://clob.polymarket.com"
    # Gamma API端点 (用于获取市场信息)
    GAMMA_HOST = "https://gamma-api.polymarket.com"
    
    # Chain ID
    CHAIN_ID = 137  # Polygon mainnet
    
    def __init__(self):
        self.config = config_manager.polymarket
        self._http_client: Optional[httpx.AsyncClient] = None
        self._clob_client: Optional[ClobClient] = None
        self._account: Optional[Account] = None
    
    async def initialize(self):
        """初始化客户端"""
        self._http_client = httpx.AsyncClient(timeout=30.0)
        
        # 初始化账户和 CLOB 客户端
        if self.config.private_key:
            self._account = Account.from_key(self.config.private_key)
            logger.info(f"钱包地址: {self._account.address}")
            
            # 初始化 py_clob_client
            try:
                # 准备 API 凭证（如果有配置）
                api_creds = None
                
                # 创建 CLOB 客户端（参考 test.py 的方式）
                # signature_type: 0=EOA, 1=POLY_GNOSIS_SAFE (Email/Magic), 2=POLY_PROXY
                # 邮箱类型使用 1=POLY_GNOSIS_SAFE
                clob_kwargs = {
                    "host": self.CLOB_HOST,
                    "key": self.config.private_key,
                    "chain_id": self.CHAIN_ID,
                    "signature_type": 1,  # 1=POLY_GNOSIS_SAFE (Email/Magic登录)
                }
                # 只有在配置了 funder 时才添加
                if self.config.funder:
                    clob_kwargs["funder"] = self.config.funder

                self._clob_client = ClobClient(**clob_kwargs)
                
                # 如果没有配置 API 凭证，立即创建/派生（参考 test.py）
                if not api_creds:
                    logger.info("正在创建/派生 API 凭证...")
                    loop = asyncio.get_event_loop()
                    derived_creds = await loop.run_in_executor(
                        None,
                        lambda: self._clob_client.create_or_derive_api_creds()
                    )
                    if derived_creds:
                        self._clob_client.set_api_creds(derived_creds)
                        logger.info("API 凭证已成功创建/派生")
                    else:
                        logger.warning("API 凭证创建/派生返回空结果")
                
                logger.info("CLOB 客户端初始化成功")
            except Exception as e:
                logger.error(f"初始化 CLOB 客户端失败: {e}")
                import traceback
                logger.error(traceback.format_exc())
    
    async def close(self):
        """关闭客户端"""
        if self._http_client:
            await self._http_client.aclose()
    
    # ============ 市场相关（使用 Gamma API） ============
    
    async def get_sport_markets(self, hours_filter: float = 1.0) -> List[Market]:
        """
        获取Sport市场列表
        
        Args:
            hours_filter: 时间过滤（返回在此时间内开始或已开始的市场，比赛进行中仍可投注）
        
        Returns:
            符合条件的市场列表
        """
        try:
            # 使用 Gamma API 的 events 端点，通过 tag_slug 过滤 sport 事件
            # 查询条件：还有 hours_filter 小时内结束且活跃的体育市场
            now = datetime.utcnow()
            
            # end_date_min: 往前推1小时，以包含正在进行的比赛（比赛通常持续1-2小时）
            min_date = (now - timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%SZ')
            # end_date_max: 限制在 hours_filter 小时内结束
            max_date = (now + timedelta(hours=hours_filter)).strftime('%Y-%m-%dT%H:%M:%SZ')
            
            response = await self._http_client.get(
                f"{self.GAMMA_HOST}/events",
                params={
                    "closed": "false",
                    "active": "true",
                    "tag_slug": "sports",
                    "limit": 200,  # 按时间排序后不需要太大的 limit
                    "order": "endDate",  # 按结束时间排序，最近的在前
                    "end_date_min": min_date,  # 包含最近1小时内开始的比赛（正在进行中）
                    "end_date_max": max_date   # 限制在 hours_filter 小时内结束
                }
            )
            
            if response.status_code != 200:
                logger.error(f"获取Sport事件列表失败: {response.text}")
                return []
            
            events_data = response.json()
            markets = []
            
            # 重新获取当前时间，因为API调用可能有延迟
            now = datetime.utcnow()
            filter_threshold = now + timedelta(hours=hours_filter)
            # 允许正在进行中的比赛（最多1小时前开始）
            min_allowed_date = now - timedelta(hours=1)
            
            logger.info(f"获取到 {len(events_data)} 个Sport事件")
            logger.info(f"时间过滤: 当前时间={now.strftime('%Y-%m-%d %H:%M:%S')}, "
                       f"允许范围=[{min_allowed_date.strftime('%Y-%m-%d %H:%M:%S')}, "
                       f"{filter_threshold.strftime('%Y-%m-%d %H:%M:%S')}] (未来{hours_filter}小时内结束)")
            
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
                    
                    # 时间过滤：保留即将开始或正在进行的市场
                    # 注意：endDate 表示比赛开始时间，不是投注截止时间
                    # 如果市场 closed=False 且 active=True，即使 endDate 已过，市场仍可投注（比赛进行中）
                    
                    if end_date:
                        # 检查结束时间是否在允许范围内
                        # 允许范围：[现在-1小时, 现在+hours_filter小时]
                        # 这样可以包含正在进行的比赛（最多1小时前开始）和即将结束的比赛（未来hours_filter小时内）
                        if end_date < min_allowed_date:
                            # 结束时间太早，已过期
                            hours_since_start = (now - end_date).total_seconds() / 3600
                            stats["expired"] += 1
                            logger.debug(f"市场已过期: {m.get('question', '')[:50]}... 结束于 {hours_since_start:.1f}小时前")
                            continue
                        elif end_date > filter_threshold:
                            # 结束时间太晚，还没到尾盘时间
                            stats["too_far"] += 1
                            # 输出最近的几个市场结束时间，帮助诊断
                            if stats["too_far"] <= 3:
                                time_diff = end_date - now
                                hours_until = time_diff.total_seconds() / 3600
                                logger.debug(f"市场时间过远: {m.get('question', '')[:50]}... 结束于 {end_date.strftime('%Y-%m-%d %H:%M')} ({hours_until:.1f}小时后)")
                            continue
                        else:
                            # 时间在允许范围内
                            if end_date < now:
                                # 正在进行中的比赛
                                hours_since_start = (now - end_date).total_seconds() / 3600
                                logger.debug(f"市场正在进行: {m.get('question', '')[:50]}... 开始于 {hours_since_start:.1f}小时前")
                            else:
                                # 即将结束的比赛
                                hours_until = (end_date - now).total_seconds() / 3600
                                logger.debug(f"市场即将结束: {m.get('question', '')[:50]}... 还有 {hours_until:.1f}小时")
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
            # 使用 end_date_min 和 order=endDate 按时间排序，最近的在前
            min_date = (datetime.utcnow() - timedelta(hours=2)).strftime('%Y-%m-%dT%H:%M:%SZ')
            
            response = await self._http_client.get(
                f"{self.GAMMA_HOST}/events",
                params={
                    "closed": "false",
                    "active": "true",
                    "tag_slug": "sports",
                    "limit": limit,
                    "order": "endDate",  # 按结束时间排序
                    "end_date_min": min_date  # 包含正在进行的比赛
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
                logger.debug(f"发现符合条件市场: {market.question[:50]}... 价格: {price:.2f}")
        
        return filtered
    
    # ============ 交易相关（使用 py_clob_client） ============
    
    async def place_order(self, token_id: str, side: OrderSide, price: float, 
                         amount: float, market_order: bool = False) -> Optional[Order]:
        """
        下单（使用 py_clob_client）
        
        Args:
            token_id: Token ID
            side: 买卖方向
            price: 价格（0-100），市价订单时会被忽略
            amount: 金额（USDC）
            market_order: 是否为市价订单（True=市价，False=限价）
        
        Returns:
            订单对象
        """
        if not self._clob_client:
            logger.error("CLOB 客户端未初始化，无法下单")
            return None
        
        try:
            # 验证输入参数
            if not token_id:
                logger.error("token_id 不能为空")
                return None
            
            if amount <= 0:
                logger.error(f"金额无效: {amount} (应大于 0)")
                return None
            
            loop = asyncio.get_event_loop()
            
            if market_order:
                # 市价订单
                logger.debug(f"市价订单 - tokenID: {str(token_id)[:20]}..., amount: {amount}, side: {side.value}")

                # 创建市价订单参数
                # 市价订单使用 amount（金额），price 不设置或设为 0，系统会自动计算市场价格
                market_order_args = MarketOrderArgs(
                    token_id=str(token_id),
                    amount=amount,
                    side=side.value.upper(),
                    price=0,  # 设为 0，create_market_order 会自动计算市场价格
                    order_type=OrderType.FOK  # Fill or Kill
                )

                # 使用 post_market_order 一步完成市价订单（自动处理签名和提交）
                response = await loop.run_in_executor(
                    None,
                    lambda: self._clob_client.post_market_order(market_order_args)
                )
                
                # 处理提交响应
                if response:
                    # 响应可能是字典格式
                    if isinstance(response, dict):
                        if response.get("status") in ["success", "ok"]:
                            data = response.get("data", response)
                        elif "error" in response:
                            error_msg = response.get("error", response.get("message", "未知错误"))
                            logger.error(f"市价订单提交失败: {error_msg}")
                            return None
                        else:
                            data = response

                        # 从响应中获取订单ID
                        order_id = str(data.get("orderID", data.get("id", "")))
                        if not order_id:
                            # 如果没有ID，使用订单数据中的信息生成
                            import uuid
                            order_id = str(uuid.uuid4())

                        # 获取实际成交信息
                        actual_price = float(data.get("price", data.get("avgPrice", 0))) * 100
                        actual_size = float(data.get("size", data.get("filledSize", 0)))

                        # 计算实际金额
                        actual_amount = actual_size * actual_price / 100 if actual_price > 0 else amount

                        order = Order(
                            id=order_id,
                            market_id=str(data.get("market", "")),
                            token_id=token_id,
                            side=side,
                            price=actual_price if actual_price > 0 else price,
                            size=actual_size if actual_size > 0 else 0,
                            amount=actual_amount if actual_amount > 0 else amount,
                            status=OrderStatus.OPEN
                        )
                        logger.debug(f"市价订单成功 - 订单ID: {order.id}, 成交价格: {actual_price:.2f}¢, 数量: {actual_size:.4f}")
                        return order
                    else:
                        logger.error(f"市价订单响应格式未知: {type(response)}")
                        return None
                else:
                    logger.error("市价订单提交失败: 无响应")
                    return None
            else:
                # 限价订单
                if price <= 0 or price >= 100:
                    logger.error(f"价格无效: {price} (应在 0-100 之间)")
                    return None

                # 将价格转换为0-1范围
                price_decimal = price / 100

                # 验证价格范围
                if price_decimal <= 0 or price_decimal >= 1:
                    logger.error(f"价格超出范围: {price_decimal} (应在 0-1 之间)")
                    return None

                # 计算数量
                size = amount / price_decimal

                if size <= 0:
                    logger.error(f"计算出的数量无效: {size}")
                    return None

                # 创建限价订单参数
                order_args = OrderArgs(
                    token_id=str(token_id),
                    price=price_decimal,
                    size=size,
                    side=side.value.upper()
                )

                logger.debug(f"限价订单 - tokenID: {str(token_id)[:20]}..., price: {price_decimal}, size: {size}, side: {side.value}")

                # 使用 create_and_post_order 一步完成（自动处理签名）
                response = await loop.run_in_executor(
                    None,
                    lambda: self._clob_client.create_and_post_order(order_args)
                )

                # 处理响应（py_clob_client 可能返回不同的格式）
                if response:
                    # 检查响应格式
                    if isinstance(response, dict):
                        # 如果响应包含 status 字段
                        if response.get("status") in ["success", "ok"]:
                            data = response.get("data", response)
                        elif "error" in response:
                            error_msg = response.get("error", response.get("message", "未知错误"))
                            logger.error(f"限价订单失败: {error_msg}")
                            logger.error(f"订单参数: {order_args}")
                            return None
                        else:
                            # 直接是订单数据
                            data = response
                    else:
                        # 响应可能直接是订单数据
                        data = response

                    # 构建订单对象
                    order = Order(
                        id=str(data.get("orderID", data.get("id", ""))),
                        market_id=str(data.get("market", "")),
                        token_id=token_id,
                        side=side,
                        price=price,
                        size=size,
                        amount=amount,
                        status=OrderStatus.OPEN
                    )
                    logger.debug(LogMessages.ORDER_SUCCESS.format(
                        order_id=order.id, market_id=order.market_id
                    ))
                    return order
                else:
                    logger.error("限价订单失败: 无响应")
                    logger.error(f"订单参数: {order_args}")
                    return None
                
        except Exception as e:
            logger.error(LogMessages.ORDER_FAILED.format(market_id="", reason=str(e)))
            import traceback
            logger.error(traceback.format_exc())
            return None
    
    async def cancel_order(self, order_id: str) -> bool:
        """取消订单（使用 py_clob_client）"""
        if not self._clob_client:
            return False
        
        try:
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: self._clob_client.cancel(order_id)
            )
            
            # 处理响应
            if response:
                if isinstance(response, dict):
                    status = response.get("status", "")
                    if status in ["success", "ok"] or "orderID" in response or "id" in response:
                        logger.debug(LogMessages.ORDER_CANCELLED.format(order_id=order_id))
                        return True
                else:
                    # 如果响应不是字典，可能直接成功
                    logger.debug(LogMessages.ORDER_CANCELLED.format(order_id=order_id))
                    return True
            return False
            
        except Exception as e:
            logger.error(f"取消订单失败: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False
    
    async def get_open_orders(self) -> List[Dict]:
        """获取挂单（使用 py_clob_client）"""
        if not self._clob_client or not self._account:
            return []
        
        try:
            from py_clob_client.clob_types import OpenOrderParams
            
            # 创建查询参数（可选，不传参数会获取所有订单）
            params = OpenOrderParams()
            
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: self._clob_client.get_orders(params)
            )
            
            if response:
                if isinstance(response, dict):
                    return response.get("data", response.get("orders", []))
                elif isinstance(response, list):
                    return response
            return []
            
        except Exception as e:
            logger.error(f"获取挂单失败: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return []
    
    # ============ 账户相关（使用 py_clob_client） ============
    
    async def get_balance(self) -> Balance:
        """获取账户余额（使用 py_clob_client 的 get_balance_allowance 方法）"""
        if not self._account or not self._clob_client:
            return Balance()
        
        max_retries = 3
        for attempt in range(max_retries):
            try:
                # 使用 py_clob_client 的 get_balance_allowance 方法
                # 这个方法需要 Level 2 认证，返回余额和授权信息
                # 参考 test.py，使用 AssetType.COLLATERAL
                params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(
                    None,
                    lambda: self._clob_client.get_balance_allowance(params=params)
                )
                
                if result:
                    # USDC 有 6 位小数，需要除以 10^6 转换为实际金额
                    USDC_DECIMALS = 10 ** 6
                    
                    # 解析返回的数据
                    if isinstance(result, dict):
                        # 尝试不同的字段名
                        # 原始值（以最小单位返回，如 28439549 表示 $28.439549）
                        balance_raw = float(result.get("balance", result.get("available", result.get("free", 0))))
                        allowance_raw = float(result.get("allowance", result.get("locked", result.get("reserved", 0))))
                        
                        # 转换为实际 USDC 金额（除以 10^6）
                        balance = balance_raw / USDC_DECIMALS
                        allowance = allowance_raw / USDC_DECIMALS
                        available = balance - allowance if balance >= allowance else balance
                        
                        logger.debug(f"余额原始值: balance={balance_raw}, allowance={allowance_raw}, 换算后: balance=${balance:.2f}, allowance=${allowance:.2f}")
                        
                        return Balance(
                            available=available,
                            locked=allowance,
                            total=balance
                        )
                    elif isinstance(result, (int, float)):
                        # 如果直接返回数字，也需要换算
                        balance_raw = float(result)
                        balance = balance_raw / USDC_DECIMALS
                        
                        logger.debug(f"余额原始值: {balance_raw}, 换算后: ${balance:.2f}")
                        
                        return Balance(
                            available=balance,
                            locked=0,
                            total=balance
                        )
                
                logger.warning(f"get_balance_allowance 返回空结果")
                if attempt < max_retries - 1:
                    await asyncio.sleep(1 * (attempt + 1))
                    continue
                
                return Balance()
                
            except Exception as e:
                error_msg = str(e)
                # 如果是认证错误，记录但不重试
                if "auth" in error_msg.lower() or "unauthorized" in error_msg.lower() or "401" in error_msg:
                    logger.error(f"获取余额失败: 认证错误 - {e}")
                    return Balance()
                
                logger.warning(f"获取余额失败: {e} (尝试 {attempt + 1}/{max_retries})")
                if attempt < max_retries - 1:
                    await asyncio.sleep(2 * (attempt + 1))
                    continue
                else:
                    logger.error(f"获取余额失败: 重试 {max_retries} 次后仍然失败")
                    import traceback
                    logger.error(traceback.format_exc())
                    return Balance()
        
        return Balance()
    
    async def get_positions(self) -> List[Position]:
        """获取持仓（使用原始 API 调用）"""
        if not self._account:
            return []
        
        max_retries = 3
        for attempt in range(max_retries):
            try:
                # py_clob_client 可能没有 get_positions 方法，使用原始 API 调用
                response = await self._http_client.get(
                    f"{self.CLOB_HOST}/positions",
                    params={"address": self._account.address},
                    timeout=30.0
                )
                
                positions = []
                if response.status_code == 200:
                    data = response.json()
                    if isinstance(data, list):
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
                    elif isinstance(data, dict):
                        # 如果返回的是字典格式
                        pos_list = data.get("data", data.get("positions", []))
                        for p in pos_list:
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
                
            except (httpx.ConnectError, httpx.TimeoutException) as e:
                error_type = "连接错误" if isinstance(e, httpx.ConnectError) else "超时错误"
                logger.warning(f"获取持仓失败 ({error_type}): {e} (尝试 {attempt + 1}/{max_retries})")
                if attempt < max_retries - 1:
                    await asyncio.sleep(2 * (attempt + 1))  # 递增延迟
                    continue
                else:
                    logger.error(f"获取持仓失败: 重试 {max_retries} 次后仍然失败")
                    return []
            except Exception as e:
                logger.error(f"获取持仓失败: {e}")
                import traceback
                logger.error(traceback.format_exc())
                return []
        
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
