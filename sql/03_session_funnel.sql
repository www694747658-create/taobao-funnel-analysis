-- ============================================================================
-- 03 · 会话切分与限时转化漏斗（在 02 之后执行）
-- ============================================================================
-- 为什么要切会话：
--   之前的漏斗是"9 天累计发生过"口径——只要用户 9 天内浏览过、也收藏/加购过、
--   也购买过就算转化，不管中间隔了多久。这样算出的 64.86% 明显虚高：
--   用户 11-25 随便逛了逛、12-03 才下单，也被算成"转化"，没有业务意义。
--   业界标准做法是按"超过 N 分钟不活跃"切会话，在单个会话内看漏斗。
--
-- 本脚本的设计：
--   session_summary 是**预聚合宽表**（每个会话一行，几十万量级）。
--   漏斗视图直接查它，毫秒级返回；如果让视图每次都去扫百万行明细，
--   即使有覆盖索引也要 1 秒以上。这也是数仓里常见的"明细 + 汇总"分层做法。
-- ============================================================================

USE taobao_funnel;

-- ----------------------------------------------------------------------------
-- 会话汇总宽表：会话内的用户、起止时间、各阶段标记与最早时间
-- ----------------------------------------------------------------------------
DROP TABLE IF EXISTS session_summary;
CREATE TABLE session_summary (
  session_id BIGINT UNSIGNED NOT NULL              COMMENT '会话 ID',
  user_id    INT UNSIGNED    NOT NULL              COMMENT '用户 ID（一个会话只属于一个用户）',
  t_start    DATETIME        NOT NULL              COMMENT '会话开始时间（北京时间）',
  t_end      DATETIME        NOT NULL              COMMENT '会话结束时间（北京时间）',
  duration_s INT UNSIGNED    NOT NULL              COMMENT '会话时长（秒）',
  events     INT UNSIGNED    NOT NULL              COMMENT '会话内行为数',
  has_pv     TINYINT         NOT NULL              COMMENT '会话内是否有浏览',
  has_mid    TINYINT         NOT NULL              COMMENT '会话内是否有收藏/加购',
  has_buy    TINYINT         NOT NULL              COMMENT '会话内是否有购买',
  t_pv       DATETIME        NULL                  COMMENT '会话内首次浏览时间',
  t_mid      DATETIME        NULL                  COMMENT '会话内首次收藏/加购时间',
  t_buy      DATETIME        NULL                  COMMENT '会话内首次购买时间',
  ord_mid    TINYINT         NOT NULL              COMMENT '是否"先浏览、后收藏/加购"（会话内顺序）',
  ord_buy    TINYINT         NOT NULL              COMMENT '是否走完"浏览->收藏/加购->购买"（会话内顺序）',
  PRIMARY KEY (session_id),
  KEY idx_ss_user (user_id),
  KEY idx_ss_ord  (ord_buy, ord_mid)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='会话汇总宽表（30 分钟不活跃切分）';

INSERT INTO session_summary
SELECT
  s.session_id, s.user_id, s.t_start, s.t_end,
  TIMESTAMPDIFF(SECOND, s.t_start, s.t_end)                              AS duration_s,
  s.events, s.has_pv, s.has_mid, s.has_buy, s.t_pv, s.t_mid, s.t_buy,
  -- has_* 只会是 0/1；当两级都存在时，对应时间必非 NULL，故结果不会是 NULL
  (s.has_pv = 1 AND s.has_mid = 1 AND s.t_mid >= s.t_pv)                 AS ord_mid,
  (s.has_pv = 1 AND s.has_mid = 1 AND s.has_buy = 1
   AND s.t_mid >= s.t_pv AND s.t_buy >= s.t_mid)                         AS ord_buy
FROM (
  SELECT
    session_id,
    MIN(user_id)                                                  AS user_id,
    MIN(dt)                                                       AS t_start,
    MAX(dt)                                                       AS t_end,
    COUNT(*)                                                      AS events,
    MAX(behavior_type = 'pv')                                     AS has_pv,
    MAX(behavior_type IN ('fav','cart'))                          AS has_mid,
    MAX(behavior_type = 'buy')                                    AS has_buy,
    MIN(CASE WHEN behavior_type = 'pv'            THEN dt END)     AS t_pv,
    MIN(CASE WHEN behavior_type IN ('fav','cart') THEN dt END)     AS t_mid,
    MIN(CASE WHEN behavior_type = 'buy'           THEN dt END)     AS t_buy
  FROM user_behavior
  GROUP BY session_id
) s;

-- ----------------------------------------------------------------------------
-- 视图 6：会话统计（会话数、深度、时长、单事件会话占比）
-- ----------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_session_stats AS
SELECT
  COUNT(*)                                                    AS sessions,
  COUNT(DISTINCT user_id)                                     AS users,
  ROUND(COUNT(*) / COUNT(DISTINCT user_id), 2)                AS sessions_per_user,
  ROUND(AVG(events), 2)                                       AS avg_events,
  MAX(events)                                                 AS max_events,
  ROUND(AVG(duration_s), 0)                                   AS avg_duration_s,
  SUM(events = 1)                                             AS single_event_sessions,
  ROUND(SUM(events = 1) * 100.0 / COUNT(*), 2)                AS single_event_pct
FROM session_summary;

-- ----------------------------------------------------------------------------
-- 视图 7：会话级漏斗（分母 = 含浏览的会话）
--   nested_*  = 嵌套"发生过"口径：同一会话内都出现过即算，不看先后
--   ordered_* = 严格顺序口径：会话内必须 浏览 <= 收藏/加购 <= 购买
-- ----------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_session_funnel AS
SELECT
  COUNT(*)                                                        AS sessions_with_pv,
  SUM(has_mid)                                                    AS nested_stage_2,
  SUM(has_mid = 1 AND has_buy = 1)                                AS nested_stage_3,
  SUM(ord_mid)                                                    AS ordered_stage_2,
  SUM(ord_buy)                                                    AS ordered_stage_3,
  ROUND(SUM(ord_mid) * 100.0 / COUNT(*), 2)                       AS ordered_conv_1_to_2_pct,
  ROUND(SUM(ord_buy) * 100.0 / NULLIF(SUM(ord_mid), 0), 2)        AS ordered_conv_2_to_3_pct,
  ROUND(SUM(ord_buy) * 100.0 / COUNT(*), 2)                       AS ordered_conv_1_to_3_pct
FROM session_summary
WHERE has_pv = 1;

-- ----------------------------------------------------------------------------
-- 视图 8：用户级漏斗（要求**同一个会话内**走完，最有业务意义的口径）
-- ----------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_user_session_funnel AS
SELECT
  COUNT(*)                                                       AS users_with_pv,
  SUM(any_ord_mid)                                               AS users_pv_then_mid_in_one_session,
  SUM(any_ord_buy)                                               AS users_completing_funnel_in_one_session,
  ROUND(SUM(any_ord_mid) * 100.0 / COUNT(*), 2)                  AS conv_1_to_2_pct,
  ROUND(SUM(any_ord_buy) * 100.0 / NULLIF(SUM(any_ord_mid), 0), 2) AS conv_2_to_3_pct,
  ROUND(SUM(any_ord_buy) * 100.0 / COUNT(*), 2)                  AS conv_1_to_3_pct
FROM (
  SELECT user_id,
         MAX(has_pv)  AS any_pv,
         MAX(ord_mid) AS any_ord_mid,
         MAX(ord_buy) AS any_ord_buy
  FROM session_summary
  GROUP BY user_id
) u
WHERE any_pv = 1;

-- ----------------------------------------------------------------------------
-- 视图 9：五种统计口径横向对比（一眼看清"口径决定结论"）
--   命名规则：前半段说明统计范围，后半段说明判定方式；不用编号、不用行话，
--   避免"嵌套发生过""严格顺序"这类需要额外解释的术语。
-- ----------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_funnel_caliber_compare AS
SELECT '九天累计 · 出现过就算'   AS 统计口径, conv_pv_to_buy_pct AS 转化率 FROM v_funnel_metrics
UNION ALL SELECT '九天累计 · 按先后顺序', conv_1_to_3_pct FROM v_funnel_ordered
UNION ALL SELECT '单次会话 · 出现过就算', (SELECT ROUND(SUM(has_mid=1 AND has_buy=1)*100.0/COUNT(*),2)
                                          FROM session_summary WHERE has_pv=1)
UNION ALL SELECT '单次会话 · 按先后顺序', ordered_conv_1_to_3_pct FROM v_session_funnel
UNION ALL SELECT '用户维度 · 会话内按序', conv_1_to_3_pct FROM v_user_session_funnel;
