-- ============================================================================
-- 04 · 业务洞察视图（由 src/taobao_insights.py 在写入五张洞察表之后执行）
--   依赖：session_classic / user_classic / user_profile / item_profile / item_assoc
-- ============================================================================

-- 会话级经典四层漏斗：浏览 -> 收藏 -> 加购 -> 购买（严格时序）
CREATE OR REPLACE VIEW v_classic_funnel_session AS
SELECT
  COUNT(*)                                            AS n_pv,
  SUM(t_fav  IS NOT NULL)                             AS n_fav,
  SUM(t_cart IS NOT NULL)                             AS n_cart,
  SUM(t_buy  IS NOT NULL)                             AS n_buy,
  ROUND(SUM(t_fav  IS NOT NULL) * 100.0 / COUNT(*), 2)             AS conv_pv_fav,
  ROUND(SUM(t_cart IS NOT NULL) * 100.0 / NULLIF(SUM(t_fav  IS NOT NULL), 0), 2) AS conv_fav_cart,
  ROUND(SUM(t_buy  IS NOT NULL) * 100.0 / NULLIF(SUM(t_cart IS NOT NULL), 0), 2) AS conv_cart_buy,
  ROUND(SUM(t_buy  IS NOT NULL) * 100.0 / COUNT(*), 2)             AS conv_total
FROM session_classic;

-- 用户级经典四层漏斗：九天累计、严格时序
CREATE OR REPLACE VIEW v_classic_funnel_user AS
SELECT
  COUNT(*)                                            AS n_pv,
  SUM(t_fav  IS NOT NULL)                             AS n_fav,
  SUM(t_cart IS NOT NULL)                             AS n_cart,
  SUM(t_buy  IS NOT NULL)                             AS n_buy,
  ROUND(SUM(t_fav  IS NOT NULL) * 100.0 / COUNT(*), 2)             AS conv_pv_fav,
  ROUND(SUM(t_cart IS NOT NULL) * 100.0 / NULLIF(SUM(t_fav  IS NOT NULL), 0), 2) AS conv_fav_cart,
  ROUND(SUM(t_buy  IS NOT NULL) * 100.0 / NULLIF(SUM(t_cart IS NOT NULL), 0), 2) AS conv_cart_buy,
  ROUND(SUM(t_buy  IS NOT NULL) * 100.0 / COUNT(*), 2)             AS conv_total
FROM user_classic;

-- 冲动型 vs 理性型：规模与最终购买转化率
CREATE OR REPLACE VIEW v_consumer_type AS
SELECT
  consumer_type,
  COUNT(*)                                        AS users,
  SUM(has_buy)                                    AS buyers,
  ROUND(SUM(has_buy) * 100.0 / COUNT(*), 2)       AS buy_rate_pct,
  ROUND(AVG(avg_duration_s), 0)                   AS avg_duration_s,
  ROUND(AVG(avg_events), 2)                       AS avg_events,
  ROUND(AVG(buy_cnt), 2)                          AS avg_buys,
  ROUND(AVG(sessions), 2)                         AS avg_sessions,
  ROUND(SUM(impulse_buys) * 100.0 / NULLIF(SUM(buy_sessions), 0), 2) AS impulse_buy_pct
FROM user_profile
GROUP BY consumer_type;

-- 逐时行为分布：浏览 / 购买 / 独立用户
CREATE OR REPLACE VIEW v_hourly_behavior AS
SELECT
  HOUR(dt)                                   AS hour_of_day,
  COUNT(*)                                   AS events,
  SUM(behavior_type = 'pv')                  AS pv,
  SUM(behavior_type = 'buy')                 AS buys,
  COUNT(DISTINCT user_id)                    AS users
FROM user_behavior
GROUP BY HOUR(dt);

-- 逐时 × 消费者类型：购买用户构成（高峰/低谷主力群体分析）
CREATE OR REPLACE VIEW v_peak_consumer AS
SELECT
  HOUR(ub.dt)      AS hour_of_day,
  up.consumer_type,
  COUNT(DISTINCT ub.user_id) AS buyers,
  COUNT(*)                   AS buys
FROM user_behavior ub
JOIN user_profile   up ON up.user_id = ub.user_id
WHERE ub.behavior_type = 'buy'
GROUP BY HOUR(ub.dt), up.consumer_type;

-- 逐时 × 高价值标记：购买用户构成
CREATE OR REPLACE VIEW v_peak_value AS
SELECT
  HOUR(ub.dt)      AS hour_of_day,
  up.is_high_value,
  COUNT(DISTINCT ub.user_id) AS buyers,
  COUNT(*)                   AS buys
FROM user_behavior ub
JOIN user_profile   up ON up.user_id = ub.user_id
WHERE ub.behavior_type = 'buy'
GROUP BY HOUR(ub.dt), up.is_high_value;

-- 高购买潜力用户榜（前 200）
CREATE OR REPLACE VIEW v_high_potential_users AS
SELECT
  user_id, potential_score, fav_cnt, cart_cnt, sessions,
  avg_events, avg_duration_s, top_category_id, top_cat_share
FROM user_profile
WHERE is_high_potential = 1
ORDER BY potential_score DESC
LIMIT 200;

-- 高价值用户榜（前 200，含偏好类目）
CREATE OR REPLACE VIEW v_high_value_users AS
SELECT
  user_id, rfm_score, buy_cnt, pv_cnt, fav_cnt, cart_cnt,
  active_days, sessions, top_category_id, top_cat_share, consumer_type
FROM user_profile
WHERE is_high_value = 1
ORDER BY rfm_score DESC
LIMIT 200;

-- 商品分级汇总
CREATE OR REPLACE VIEW v_item_grade_summary AS
SELECT
  grade,
  COUNT(*)                    AS items,
  SUM(pv)                     AS pv,
  SUM(buy)                    AS buy,
  ROUND(AVG(conv_pct), 2)     AS avg_conv_pct,
  ROUND(AVG(fav_rate), 2)     AS avg_fav_rate,
  ROUND(AVG(cart_rate), 2)    AS avg_cart_rate
FROM item_profile
GROUP BY grade;

-- 低购买率商品榜：流量不低但转化垫底，附流失定位（前 50）
CREATE OR REPLACE VIEW v_item_low_list AS
SELECT
  item_id, category_id, pv, fav, cart, buy, conv_pct, worst_stage,
  ROUND((fav + cart) * 100.0 / pv, 2)            AS intent_rate_pct,
  ROUND(buy * 100.0 / NULLIF(fav + cart, 0), 2)  AS intent_to_buy_pct
FROM item_profile
WHERE grade = '低购买率'
ORDER BY pv DESC
LIMIT 50;

-- 高购买率商品的类目分布（前 10）：共性分析
CREATE OR REPLACE VIEW v_item_high_category AS
SELECT
  category_id,
  COUNT(*)                 AS items,
  SUM(buy)                 AS buys,
  ROUND(AVG(conv_pct), 2)  AS avg_conv_pct
FROM item_profile
WHERE grade = '高购买率'
GROUP BY category_id
ORDER BY items DESC
LIMIT 10;

-- 热门共购组合（前 50）：关联推荐示例
CREATE OR REPLACE VIEW v_item_assoc_top AS
SELECT item_id, rel_item_id, co_buy
FROM item_assoc
ORDER BY co_buy DESC
LIMIT 50;
