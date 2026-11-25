-- ============================================================================
-- 02 · 二级索引与漏斗视图（在 LOAD DATA 完成之后执行）
--   先导入后建索引，比"带索引导入"快很多；索引按后续漏斗/路径分析的查询设计
-- ============================================================================

USE taobao_funnel;

-- ----------------------------------------------------------------------------
-- 二级索引
--
-- 关键点：idx_user_behavior_dt 是**覆盖索引**（聚合要用的三列都放进索引）。
--   漏斗视图需要对全表按 user_id 分组并读取 behavior_type、dt；
--   若只有 idx_user_dt(user_id, dt)，MySQL 必须为每一行回表随机读聚簇索引，
--   实测该聚合要 62 秒；加上覆盖索引后降到 0.85 秒（约 70 倍）。
-- ----------------------------------------------------------------------------
ALTER TABLE user_behavior
  ADD INDEX idx_user_dt         (user_id, dt),                 -- 取某用户的行为时间序列（漏斗路径）
  ADD INDEX idx_user_behavior_dt(user_id, behavior_type, dt),  -- 覆盖索引：用户级漏斗聚合不再回表
  ADD INDEX idx_user_cat        (user_id, category_id),        -- 覆盖索引：用户偏好类目分组不再回表
  ADD INDEX idx_session_bhv_dt  (session_id, behavior_type, dt), -- 覆盖索引：会话汇总（03 脚本）不再回表
  ADD INDEX idx_behavior_dt     (behavior_type, dt),           -- 按行为类型 + 时间范围筛选
  ADD INDEX idx_dt              (dt),                          -- 全站时间范围 / 逐日逐时统计
  ADD INDEX idx_item_cat_bhv    (item_id, category_id, behavior_type), -- 覆盖索引：商品行为汇总与主导类目不再回表
  ADD INDEX idx_category        (category_id);                 -- 类目维度分析

-- ----------------------------------------------------------------------------
-- 视图 1：每个用户的行为阶段标记 + 各阶段最早时间（漏斗计算基础）
-- ----------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_user_stage AS
SELECT
  user_id,
  MAX(behavior_type = 'pv')                AS has_pv,
  MAX(behavior_type IN ('fav', 'cart'))    AS has_mid,
  MAX(behavior_type = 'buy')               AS has_buy,
  MIN(CASE WHEN behavior_type = 'pv'            THEN dt END) AS t_pv,
  MIN(CASE WHEN behavior_type IN ('fav','cart') THEN dt END) AS t_mid,
  MIN(CASE WHEN behavior_type = 'buy'           THEN dt END) AS t_buy,
  COUNT(*)                                 AS events
FROM user_behavior
GROUP BY user_id;

-- ----------------------------------------------------------------------------
-- 视图 2：转化漏斗核心指标（嵌套"发生过"口径）
--   浏览 PV -> 浏览且收藏/加购 -> 浏览且收藏/加购且购买
-- ----------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_funnel_metrics AS
SELECT
  COUNT(*)                                       AS pv_users,
  SUM(has_mid)                                   AS pv_and_mid_users,
  SUM(has_mid = 1 AND has_buy = 1)               AS pv_mid_buy_users,
  ROUND(SUM(has_mid) * 100.0 / COUNT(*), 2)                          AS conv_pv_to_mid_pct,
  ROUND(SUM(has_mid = 1 AND has_buy = 1) * 100.0 / SUM(has_mid), 2)  AS conv_mid_to_buy_pct,
  ROUND(SUM(has_mid = 1 AND has_buy = 1) * 100.0 / COUNT(*), 2)      AS conv_pv_to_buy_pct
FROM v_user_stage
WHERE has_pv = 1;

-- ----------------------------------------------------------------------------
-- 视图 3：严格顺序漏斗（要求 浏览 <= 收藏/加购 <= 购买）
--   注意：这仍是 9 天累计口径、没有会话约束，所以转化率偏高，
--   不能直接当业务漏斗指标；真实业务漏斗需按会话切分（见 README）。
-- ----------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_funnel_ordered AS
SELECT
  COUNT(*)                                                      AS pv_users,
  SUM(t_mid IS NOT NULL AND t_mid >= t_pv)                      AS pv_then_mid_users,
  SUM(t_mid IS NOT NULL AND t_mid >= t_pv
      AND t_buy IS NOT NULL AND t_buy >= t_mid)                 AS pv_mid_then_buy_users,
  ROUND(SUM(t_mid IS NOT NULL AND t_mid >= t_pv) * 100.0 / COUNT(*), 2) AS conv_1_to_2_pct,
  ROUND(SUM(t_mid IS NOT NULL AND t_mid >= t_pv
            AND t_buy IS NOT NULL AND t_buy >= t_mid) * 100.0
        / NULLIF(SUM(t_mid IS NOT NULL AND t_mid >= t_pv), 0), 2)    AS conv_2_to_3_pct,
  ROUND(SUM(t_mid IS NOT NULL AND t_mid >= t_pv
            AND t_buy IS NOT NULL AND t_buy >= t_mid) * 100.0
        / COUNT(*), 2)                                               AS conv_1_to_3_pct
FROM v_user_stage
WHERE has_pv = 1;

-- ----------------------------------------------------------------------------
-- 视图 4：四类行为的次数与独立用户数
-- ----------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_behavior_summary AS
SELECT
  behavior_type,
  COUNT(*)                    AS events,
  COUNT(DISTINCT user_id)     AS users,
  ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER (), 4) AS events_pct
FROM user_behavior
GROUP BY behavior_type;

-- ----------------------------------------------------------------------------
-- 视图 5：逐日 × 行为类型分布（趋势分析用）
-- ----------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_daily_behavior AS
SELECT
  DATE(dt)        AS d,
  behavior_type,
  COUNT(*)        AS events,
  COUNT(DISTINCT user_id) AS users
FROM user_behavior
GROUP BY DATE(dt), behavior_type;
