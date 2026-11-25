-- ============================================================================
-- 05 · 业务洞察计算（全部在 MySQL 内用集合运算与窗口函数完成）
--   由 src/taobao_insights.py 在建成五张空表之后整段执行。
--   输入：user_behavior（明细）、session_summary（会话宽表）
--   产出：session_classic / user_classic / user_profile / item_profile / item_assoc
--
--   设计目标：不再把百万级明细拉进 pandas，全部聚合、排序、分位都在库里完成，
--   让分析层从分钟级降到秒级。
--
--   分位口径说明：
--   * 相对分位（百分位）用「平均秩 / 总人数」还原 pandas 的 rank(pct=True, average)，
--     行内以 RANK() 取并列组最小秩、再补 (并列个数-1)/2，即并列组的平均秩。
--   * 绝对分位（quantile）用「线性插值」还原 pandas 的 quantile 默认算法：
--     位置 pos = q·(n-1)，取 floor/ceil 两处的值做线性插值。
-- ============================================================================

-- ================================================================ 一、经典四层漏斗
-- 会话级：浏览 -> 收藏 -> 加购 -> 购买，要求严格时序（贪婪取每步最早完成时间）
DROP TEMPORARY TABLE IF EXISTS sc1, sc2, sc3, sc4;

-- sc1 直接取会话宽表已有的首次浏览时间（03 脚本已算好），
-- 无需再对全表明细做一次 pv 分组（省去一次百万行级扫描与分组）
CREATE TEMPORARY TABLE sc1 AS
SELECT session_id, user_id, t_pv FROM session_summary WHERE has_pv = 1;
ALTER TABLE sc1 ADD PRIMARY KEY (session_id);

CREATE TEMPORARY TABLE sc2 AS
SELECT b.session_id, MIN(b.dt) AS t_fav
FROM user_behavior b JOIN sc1 ON sc1.session_id = b.session_id
WHERE b.behavior_type = 'fav' AND b.dt >= sc1.t_pv
GROUP BY b.session_id;
ALTER TABLE sc2 ADD PRIMARY KEY (session_id);

CREATE TEMPORARY TABLE sc3 AS
SELECT b.session_id, MIN(b.dt) AS t_cart
FROM user_behavior b JOIN sc2 ON sc2.session_id = b.session_id
WHERE b.behavior_type = 'cart' AND b.dt >= sc2.t_fav
GROUP BY b.session_id;
ALTER TABLE sc3 ADD PRIMARY KEY (session_id);

CREATE TEMPORARY TABLE sc4 AS
SELECT b.session_id, MIN(b.dt) AS t_buy
FROM user_behavior b JOIN sc3 ON sc3.session_id = b.session_id
WHERE b.behavior_type = 'buy' AND b.dt >= sc3.t_cart
GROUP BY b.session_id;
ALTER TABLE sc4 ADD PRIMARY KEY (session_id);

INSERT INTO session_classic (session_id, user_id, t_pv, t_fav, t_cart, t_buy)
SELECT sc1.session_id, sc1.user_id, sc1.t_pv, sc2.t_fav, sc3.t_cart, sc4.t_buy
FROM sc1
LEFT JOIN sc2 ON sc2.session_id = sc1.session_id
LEFT JOIN sc3 ON sc3.session_id = sc1.session_id
LEFT JOIN sc4 ON sc4.session_id = sc1.session_id;

-- 用户级：九天累计、严格时序（同样从浏览起步）
DROP TEMPORARY TABLE IF EXISTS uc1, uc2, uc3, uc4;

-- uc1 同样改为聚合会话宽表（MIN 各会话首次浏览时间 = 该用户九天内首次浏览）
CREATE TEMPORARY TABLE uc1 AS
SELECT user_id, MIN(t_pv) AS t_pv FROM session_summary
WHERE has_pv = 1 GROUP BY user_id;
ALTER TABLE uc1 ADD PRIMARY KEY (user_id);

CREATE TEMPORARY TABLE uc2 AS
SELECT b.user_id, MIN(b.dt) AS t_fav
FROM user_behavior b JOIN uc1 ON uc1.user_id = b.user_id
WHERE b.behavior_type = 'fav' AND b.dt >= uc1.t_pv
GROUP BY b.user_id;
ALTER TABLE uc2 ADD PRIMARY KEY (user_id);

CREATE TEMPORARY TABLE uc3 AS
SELECT b.user_id, MIN(b.dt) AS t_cart
FROM user_behavior b JOIN uc2 ON uc2.user_id = b.user_id
WHERE b.behavior_type = 'cart' AND b.dt >= uc2.t_fav
GROUP BY b.user_id;
ALTER TABLE uc3 ADD PRIMARY KEY (user_id);

CREATE TEMPORARY TABLE uc4 AS
SELECT b.user_id, MIN(b.dt) AS t_buy
FROM user_behavior b JOIN uc3 ON uc3.user_id = b.user_id
WHERE b.behavior_type = 'buy' AND b.dt >= uc3.t_cart
GROUP BY b.user_id;
ALTER TABLE uc4 ADD PRIMARY KEY (user_id);

INSERT INTO user_classic (user_id, t_pv, t_fav, t_cart, t_buy)
SELECT uc1.user_id, uc1.t_pv, uc2.t_fav, uc3.t_cart, uc4.t_buy
FROM uc1
LEFT JOIN uc2 ON uc2.user_id = uc1.user_id
LEFT JOIN uc3 ON uc3.user_id = uc1.user_id
LEFT JOIN uc4 ON uc4.user_id = uc1.user_id;

-- ================================================================ 二、用户画像基础聚合
DROP TEMPORARY TABLE IF EXISTS up0, up_cat, up_ss, up_med;

CREATE TEMPORARY TABLE up0 AS
SELECT
  user_id,
  COUNT(*)                   AS events,
  SUM(behavior_type = 'pv')  AS pv_cnt,
  SUM(behavior_type = 'fav') AS fav_cnt,
  SUM(behavior_type = 'cart') AS cart_cnt,
  SUM(behavior_type = 'buy') AS buy_cnt,
  COUNT(DISTINCT DATE(dt))   AS active_days,
  MAX(dt)                    AS last_dt
FROM user_behavior
GROUP BY user_id;
ALTER TABLE up0 ADD PRIMARY KEY (user_id);

-- 行为最集中的类目（占比最高的一个；并列取类目 ID 小的，保证可复现）
CREATE TEMPORARY TABLE up_cat AS
SELECT user_id, category_id, cnt FROM (
  SELECT user_id, category_id, COUNT(*) AS cnt,
         ROW_NUMBER() OVER (PARTITION BY user_id
                            ORDER BY COUNT(*) DESC, category_id) AS rn
  FROM user_behavior
  GROUP BY user_id, category_id
) t WHERE rn = 1;
ALTER TABLE up_cat ADD PRIMARY KEY (user_id);

-- 会话维度聚合：会话数、每会话行为数、每会话时长、购买会话数、冲动购买会话数、意向闭环
CREATE TEMPORARY TABLE up_ss AS
SELECT
  user_id,
  COUNT(*)       AS sessions,
  AVG(events)    AS avg_events,
  AVG(duration_s) AS avg_duration_s,
  SUM(CASE WHEN has_buy = 1 AND has_pv = 1 AND t_buy >= t_pv
           THEN 1 ELSE 0 END) AS buy_sessions,
  SUM(CASE WHEN has_buy = 1 AND has_pv = 1 AND t_buy >= t_pv
           AND TIMESTAMPDIFF(SECOND, t_pv, t_buy) <= 600
           THEN 1 ELSE 0 END) AS impulse_buys,
  MAX(CASE WHEN has_mid = 1 AND has_buy = 0 THEN 1 ELSE 0 END) AS intent_unconverted
FROM session_summary
GROUP BY user_id;
ALTER TABLE up_ss ADD PRIMARY KEY (user_id);

-- 会话时长中位数：单次超长挂机会话不该把整段决策风格带偏，故用中位数而非均值
CREATE TEMPORARY TABLE up_med AS
SELECT user_id, AVG(duration_s) AS med_duration_s FROM (
  SELECT user_id, duration_s,
         ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY duration_s) AS rn,
         COUNT(*)     OVER (PARTITION BY user_id)                     AS n
  FROM session_summary
) t
WHERE rn IN (FLOOR((n + 1) / 2), CEIL((n + 1) / 2))
GROUP BY user_id;
ALTER TABLE up_med ADD PRIMARY KEY (user_id);

-- 拼成一张宽表（含派生列：新近度天数、偏好类目占比、是否有购买）
SET @horizon_d = (SELECT DATE(MAX(dt)) FROM user_behavior);

DROP TEMPORARY TABLE IF EXISTS up_score;
CREATE TEMPORARY TABLE up_score AS
SELECT
  b.user_id, b.events, b.pv_cnt, b.fav_cnt, b.cart_cnt, b.buy_cnt,
  b.active_days, b.last_dt,
  GREATEST(DATEDIFF(@horizon_d, DATE(b.last_dt)), 0) AS recency_days,
  c.category_id AS top_category_id,
  COALESCE(c.cnt / b.events, 0) AS top_cat_share,
  COALESCE(s.sessions, 0)      AS sessions,
  COALESCE(s.avg_events, 0)    AS avg_events,
  COALESCE(s.avg_duration_s, 0) AS avg_duration_s,
  COALESCE(s.buy_sessions, 0)  AS buy_sessions,
  COALESCE(s.impulse_buys, 0)  AS impulse_buys,
  COALESCE(s.intent_unconverted, 0) AS intent_unconverted,
  COALESCE(m.med_duration_s, 0) AS med_duration_s,
  CASE WHEN b.buy_cnt >= 1 THEN 1 ELSE 0 END AS has_buy
FROM up0 b
LEFT JOIN up_cat c ON c.user_id = b.user_id
LEFT JOIN up_ss  s ON s.user_id = b.user_id
LEFT JOIN up_med m ON m.user_id = b.user_id;
ALTER TABLE up_score ADD PRIMARY KEY (user_id);

-- 相对分位（平均秩口径，等价 pandas rank(pct=True, method='average')）
DROP TEMPORARY TABLE IF EXISTS up_rank;
CREATE TEMPORARY TABLE up_rank AS
SELECT
  user_id, buy_cnt, events, avg_events, med_duration_s, recency_days,
  (RANK() OVER (ORDER BY buy_cnt)           + (COUNT(*) OVER (PARTITION BY buy_cnt) - 1) / 2.0)
    / COUNT(*) OVER () AS p_buy,
  (RANK() OVER (ORDER BY events)            + (COUNT(*) OVER (PARTITION BY events) - 1) / 2.0)
    / COUNT(*) OVER () AS p_events,
  (RANK() OVER (ORDER BY recency_days DESC) + (COUNT(*) OVER (PARTITION BY recency_days) - 1) / 2.0)
    / COUNT(*) OVER () AS p_recency,
  (RANK() OVER (ORDER BY med_duration_s)    + (COUNT(*) OVER (PARTITION BY med_duration_s) - 1) / 2.0)
    / COUNT(*) OVER () AS p_duration,
  (RANK() OVER (ORDER BY avg_events)        + (COUNT(*) OVER (PARTITION BY avg_events) - 1) / 2.0)
    / COUNT(*) OVER () AS p_depth
FROM up_score;
ALTER TABLE up_rank ADD PRIMARY KEY (user_id);

-- 综合分：新近度(负) / 购买频次 / 活跃度 加权，映射到 1~5
DROP TEMPORARY TABLE IF EXISTS up_rfm;
CREATE TEMPORARY TABLE up_rfm AS
SELECT user_id,
       (0.5 * p_buy + 0.3 * p_events + 0.2 * p_recency) * 4 + 1 AS rfm
FROM up_rank;
ALTER TABLE up_rfm ADD PRIMARY KEY (user_id);

-- 高价值分数线：购买用户 rfm 的 0.8 分位（线性插值）
DROP TEMPORARY TABLE IF EXISTS q_rfm, thr_value;
CREATE TEMPORARY TABLE q_rfm AS
SELECT
  MAX(n) AS n,
  MAX(CASE WHEN rn = FLOOR(0.8 * (n - 1)) THEN rfm END) AS lo,
  MAX(CASE WHEN rn = CEIL(0.8 * (n - 1))  THEN rfm END) AS hi
FROM (
  SELECT r.rfm, ROW_NUMBER() OVER (ORDER BY r.rfm) - 1 AS rn, COUNT(*) OVER () AS n
  FROM up_rfm r
  WHERE r.user_id IN (SELECT user_id FROM up_score WHERE has_buy = 1)
) t;

CREATE TEMPORARY TABLE thr_value AS
SELECT lo + (0.8 * (n - 1) - FLOOR(0.8 * (n - 1))) * (hi - lo) AS thr FROM q_rfm;

-- 潜力分三个 P90 归一基准（与 1 取大，防除零）
DROP TEMPORARY TABLE IF EXISTS q90_intent, q90_events, q90_dur;

CREATE TEMPORARY TABLE q90_intent AS
SELECT GREATEST(MAX(lo) + (0.9 * MAX(n) - FLOOR(0.9 * MAX(n))) * (MAX(hi) - MAX(lo)), 1) AS p90
FROM (
  SELECT MAX(CASE WHEN rn = FLOOR(0.9 * (n - 1)) THEN v END) AS lo,
         MAX(CASE WHEN rn = CEIL(0.9 * (n - 1))  THEN v END) AS hi,
         MAX(n) AS n
  FROM (SELECT (fav_cnt + cart_cnt) AS v,
               ROW_NUMBER() OVER (ORDER BY (fav_cnt + cart_cnt)) - 1 AS rn,
               COUNT(*) OVER () AS n
        FROM up_score) t
) x;

CREATE TEMPORARY TABLE q90_events AS
SELECT GREATEST(MAX(lo) + (0.9 * MAX(n) - FLOOR(0.9 * MAX(n))) * (MAX(hi) - MAX(lo)), 1) AS p90
FROM (
  SELECT MAX(CASE WHEN rn = FLOOR(0.9 * (n - 1)) THEN v END) AS lo,
         MAX(CASE WHEN rn = CEIL(0.9 * (n - 1))  THEN v END) AS hi,
         MAX(n) AS n
  FROM (SELECT avg_events AS v,
               ROW_NUMBER() OVER (ORDER BY avg_events) - 1 AS rn,
               COUNT(*) OVER () AS n
        FROM up_score) t
) x;

CREATE TEMPORARY TABLE q90_dur AS
SELECT GREATEST(MAX(lo) + (0.9 * MAX(n) - FLOOR(0.9 * MAX(n))) * (MAX(hi) - MAX(lo)), 1) AS p90
FROM (
  SELECT MAX(CASE WHEN rn = FLOOR(0.9 * (n - 1)) THEN v END) AS lo,
         MAX(CASE WHEN rn = CEIL(0.9 * (n - 1))  THEN v END) AS hi,
         MAX(n) AS n
  FROM (SELECT avg_duration_s AS v,
               ROW_NUMBER() OVER (ORDER BY avg_duration_s) - 1 AS rn,
               COUNT(*) OVER () AS n
        FROM up_score) t
) x;

-- 购买潜力分：意向闭环(25) + 意向频次(25) + 会话深度(20) + 停留时长(15) + 类目聚焦(15)
-- 单独落一张表，避免同一条语句里重复打开 up_score（MySQL 不允许重复引用同一临时表）
DROP TEMPORARY TABLE IF EXISTS up_pot;
CREATE TEMPORARY TABLE up_pot AS
SELECT
  s2.user_id,
  ROUND(
    s2.intent_unconverted * 25
    + LEAST((s2.fav_cnt + s2.cart_cnt) / (SELECT p90 FROM q90_intent), 1) * 25
    + LEAST(s2.avg_events / (SELECT p90 FROM q90_events), 1) * 20
    + LEAST(s2.avg_duration_s / (SELECT p90 FROM q90_dur), 1) * 15
    + LEAST(s2.top_cat_share, 1) * 15, 2) AS score
FROM up_score s2;
ALTER TABLE up_pot ADD PRIMARY KEY (user_id);

-- 写入用户画像。决策风格改为复合评分：
--   理性分 = 100 × ( 0.5·会话时长中位数分位 + 0.3·每会话行为数分位 + 0.2·行为规模分位 )
--   >= 50 判为理性型，否则冲动型；三项分位均为全体用户内的相对分位（平均秩口径）
INSERT INTO user_profile
(user_id, events, pv_cnt, fav_cnt, cart_cnt, buy_cnt, active_days, sessions,
 avg_events, avg_duration_s, buy_sessions, impulse_buys, has_buy, intent_unconverted,
 top_category_id, top_cat_share, consumer_type, rfm_score, is_high_value,
 potential_score, is_high_potential)
SELECT
  s.user_id, s.events, s.pv_cnt, s.fav_cnt, s.cart_cnt, s.buy_cnt,
  s.active_days, s.sessions,
  ROUND(s.avg_events, 2), ROUND(s.avg_duration_s, 1),
  s.buy_sessions, s.impulse_buys, s.has_buy, s.intent_unconverted,
  s.top_category_id, ROUND(s.top_cat_share, 4),
  CASE WHEN (0.5 * r.p_duration + 0.3 * r.p_depth + 0.2 * r.p_events) >= 0.5
       THEN '理性型' ELSE '冲动型' END,
  ROUND(f.rfm, 3),
  CASE WHEN s.has_buy = 1 AND f.rfm >= (SELECT thr FROM thr_value) THEN 1 ELSE 0 END,
  p.score,
  CASE WHEN s.intent_unconverted = 1 AND p.score >= 70.0 THEN 1 ELSE 0 END
FROM up_score s
JOIN up_rank r ON r.user_id = s.user_id
JOIN up_rfm  f ON f.user_id = s.user_id
JOIN up_pot  p ON p.user_id = s.user_id;

-- ================================================================ 三、商品分级与流失定位
DROP TEMPORARY TABLE IF EXISTS ip0, ip0_u, ip_cat, ip_base;

CREATE TEMPORARY TABLE ip0 AS
SELECT item_id,
  SUM(behavior_type = 'pv')   AS pv,
  SUM(behavior_type = 'fav')  AS fav,
  SUM(behavior_type = 'cart') AS cart,
  SUM(behavior_type = 'buy')  AS buy
FROM user_behavior
GROUP BY item_id;
ALTER TABLE ip0 ADD PRIMARY KEY (item_id);

-- 购买独立用户数单独统计：只在 buy 行（十万级）上做 DISTINCT，
-- 若与上面的全表分组放在同一条语句里，会强制对上百万行做去重排序（实测 60 秒+）
CREATE TEMPORARY TABLE ip0_u AS
SELECT item_id, COUNT(DISTINCT user_id) AS buy_users
FROM user_behavior WHERE behavior_type = 'buy' GROUP BY item_id;
ALTER TABLE ip0_u ADD PRIMARY KEY (item_id);

CREATE TEMPORARY TABLE ip_cat AS
SELECT item_id, category_id FROM (
  SELECT item_id, category_id, COUNT(*) AS c,
         ROW_NUMBER() OVER (PARTITION BY item_id ORDER BY COUNT(*) DESC, category_id) AS rn
  FROM user_behavior GROUP BY item_id, category_id
) t WHERE rn = 1;
ALTER TABLE ip_cat ADD PRIMARY KEY (item_id);

CREATE TEMPORARY TABLE ip_base AS
SELECT
  i.item_id, c.category_id, i.pv, i.fav, i.cart, i.buy,
  COALESCE(u.buy_users, 0) AS buy_users,
  ROUND(i.buy  * 100.0 / GREATEST(i.pv, 1), 4) AS conv_pct,
  ROUND(i.fav  * 100.0 / GREATEST(i.pv, 1), 4) AS fav_rate,
  ROUND(i.cart * 100.0 / GREATEST(i.pv, 1), 4) AS cart_rate,
  (i.fav + i.cart) * 1.0 / GREATEST(i.pv, 1)   AS intent_rate,
  (i.pv >= 30) AS eligible
FROM ip0 i
JOIN ip_cat c ON c.item_id = i.item_id
LEFT JOIN ip0_u u ON u.item_id = i.item_id;

-- 参评商品的四个分位：conv_pct 的 0.8 / 0.2 分位、pv 的 0.5 分位、intent_rate 的 0.5 分位
DROP TEMPORARY TABLE IF EXISTS ip_qc, ip_qp, ip_qi;

CREATE TEMPORARY TABLE ip_qc AS
SELECT
  lo_hi + (0.8 * (n - 1) - FLOOR(0.8 * (n - 1))) * (hi_hi - lo_hi) AS q_hi,
  lo_lo + (0.2 * (n - 1) - FLOOR(0.2 * (n - 1))) * (hi_lo - lo_lo) AS q_lo
FROM (
  SELECT MAX(n) AS n,
         MAX(CASE WHEN rn = FLOOR(0.8 * (n - 1)) THEN v END) AS lo_hi,
         MAX(CASE WHEN rn = CEIL(0.8 * (n - 1))  THEN v END) AS hi_hi,
         MAX(CASE WHEN rn = FLOOR(0.2 * (n - 1)) THEN v END) AS lo_lo,
         MAX(CASE WHEN rn = CEIL(0.2 * (n - 1))  THEN v END) AS hi_lo
  FROM (SELECT conv_pct AS v, ROW_NUMBER() OVER (ORDER BY conv_pct) - 1 AS rn,
               COUNT(*) OVER () AS n
        FROM ip_base WHERE eligible) t
) x;

CREATE TEMPORARY TABLE ip_qp AS
SELECT lo + (0.5 * (n - 1) - FLOOR(0.5 * (n - 1))) * (hi - lo) AS pv_med
FROM (
  SELECT MAX(n) AS n,
         MAX(CASE WHEN rn = FLOOR(0.5 * (n - 1)) THEN v END) AS lo,
         MAX(CASE WHEN rn = CEIL(0.5 * (n - 1))  THEN v END) AS hi
  FROM (SELECT pv AS v, ROW_NUMBER() OVER (ORDER BY pv) - 1 AS rn, COUNT(*) OVER () AS n
        FROM ip_base WHERE eligible) t
) x;

CREATE TEMPORARY TABLE ip_qi AS
SELECT lo + (0.5 * (n - 1) - FLOOR(0.5 * (n - 1))) * (hi - lo) AS intent_med
FROM (
  SELECT MAX(n) AS n,
         MAX(CASE WHEN rn = FLOOR(0.5 * (n - 1)) THEN v END) AS lo,
         MAX(CASE WHEN rn = CEIL(0.5 * (n - 1))  THEN v END) AS hi
  FROM (SELECT intent_rate AS v, ROW_NUMBER() OVER (ORDER BY intent_rate) - 1 AS rn,
               COUNT(*) OVER () AS n
        FROM ip_base WHERE eligible) t
) x;

-- 三个分位各只取一次，避免同一条语句里重复打开临时表（MySQL 报 1137）
SET @item_q_lo     = (SELECT q_lo FROM ip_qc);
SET @item_q_hi     = (SELECT q_hi FROM ip_qc);
SET @item_pv_med   = (SELECT pv_med FROM ip_qp);
SET @item_intent_med = (SELECT intent_med FROM ip_qi);

INSERT INTO item_profile
(item_id, category_id, pv, fav, cart, buy, buy_users, conv_pct, fav_rate, cart_rate,
 grade, worst_stage)
SELECT
  b.item_id, b.category_id, b.pv, b.fav, b.cart, b.buy, b.buy_users,
  b.conv_pct, b.fav_rate, b.cart_rate,
  CASE
    WHEN b.eligible AND b.conv_pct <= @item_q_lo
         AND b.pv >= @item_pv_med THEN '低购买率'
    WHEN b.eligible AND b.conv_pct >= @item_q_hi THEN '高购买率'
    ELSE '普通'
  END AS grade,
  CASE
    WHEN b.eligible AND b.conv_pct <= @item_q_lo
         AND b.pv >= @item_pv_med
      THEN CASE WHEN b.intent_rate < @item_intent_med
                THEN '浏览到意向' ELSE '意向到购买' END
    ELSE '—'
  END AS worst_stage
FROM ip_base b
WHERE b.eligible;   -- 只入参评商品（浏览 >= 30），低流量商品噪声大、不进商品分级

-- ================================================================ 四、商品共购关联
-- 同一用户购买了两种商品即构成一对；剔除篮子过小(1件)与异常大(>50件)的用户
DROP TEMPORARY TABLE IF EXISTS as_basket, as_basket_b, as_users, as_pairs, as_top, as_top_b;

CREATE TEMPORARY TABLE as_basket AS
SELECT DISTINCT user_id, item_id FROM user_behavior WHERE behavior_type = 'buy';
CREATE TEMPORARY TABLE as_basket_b AS
SELECT user_id, item_id FROM as_basket;

CREATE TEMPORARY TABLE as_users AS
SELECT user_id FROM as_basket GROUP BY user_id HAVING COUNT(*) BETWEEN 2 AND 50;

CREATE TEMPORARY TABLE as_pairs AS
SELECT a.item_id, b.item_id AS rel_item_id, COUNT(*) AS co_buy
FROM as_basket a
JOIN as_users u ON u.user_id = a.user_id
JOIN as_basket_b b ON b.user_id = a.user_id AND b.item_id > a.item_id
GROUP BY a.item_id, b.item_id
HAVING COUNT(*) >= 2;

CREATE TEMPORARY TABLE as_top AS
SELECT item_id, rel_item_id, co_buy FROM (
  SELECT item_id, rel_item_id, co_buy,
         ROW_NUMBER() OVER (PARTITION BY item_id ORDER BY co_buy DESC, rel_item_id) AS rn
  FROM as_pairs
) t WHERE rn <= 5;

-- 再落一份副本，因为同一条语句里不能重复打开 as_top（MySQL 报 1137）
CREATE TEMPORARY TABLE as_top_b AS
SELECT item_id, rel_item_id, co_buy FROM as_top;

-- 双向补全：A->B 与 B->A 都入表，供"买了又买"关联推荐直接查询
INSERT INTO item_assoc (item_id, rel_item_id, co_buy)
SELECT item_id, rel_item_id, MAX(co_buy) FROM (
  SELECT item_id, rel_item_id, co_buy FROM as_top
  UNION ALL
  SELECT rel_item_id AS item_id, item_id AS rel_item_id, co_buy FROM as_top_b
) x
GROUP BY item_id, rel_item_id;

-- 清理临时表，避免连接复用时残留
DROP TEMPORARY TABLE IF EXISTS sc1, sc2, sc3, sc4, uc1, uc2, uc3, uc4;
DROP TEMPORARY TABLE IF EXISTS up0, up_cat, up_ss, up_med, up_score, up_rank, up_rfm, up_pot;
DROP TEMPORARY TABLE IF EXISTS q_rfm, thr_value, q90_intent, q90_events, q90_dur;
DROP TEMPORARY TABLE IF EXISTS ip0, ip0_u, ip_cat, ip_base, ip_qc, ip_qp, ip_qi;
DROP TEMPORARY TABLE IF EXISTS as_basket, as_basket_b, as_users, as_pairs, as_top, as_top_b;
