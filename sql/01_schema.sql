-- ============================================================================
-- 01 · 建库建表（只建主键；二级索引与视图在数据导入后由 02 脚本创建，导入更快）
-- ============================================================================
-- 数据库按功能命名：taobao_funnel = 淘宝转化漏斗
-- 数据来源：UserBehavior.csv（约 1 亿行）经"短时间窗口定抽样框 + 用户级抽样 + 完整轨迹"
--           策略构建的轻量数据集
-- ============================================================================

CREATE DATABASE IF NOT EXISTS taobao_funnel
  DEFAULT CHARACTER SET utf8mb4
  DEFAULT COLLATE utf8mb4_general_ci;

USE taobao_funnel;

-- ----------------------------------------------------------------------------
-- 主表：用户行为明细
--   抽样框 = 窗口内（首个自然日）活跃用户；按用户均匀随机抽取一部分；
--   保留这些用户的**全部行为记录**，因此每个人的漏斗链路完整，转化率无偏
-- ----------------------------------------------------------------------------
DROP TABLE IF EXISTS user_behavior;
CREATE TABLE user_behavior (
  id            BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '自增主键',
  user_id       INT UNSIGNED    NOT NULL                COMMENT '用户 ID',
  item_id       INT UNSIGNED    NOT NULL                COMMENT '商品 ID',
  category_id   INT UNSIGNED    NOT NULL                COMMENT '商品类目 ID',
  behavior_type ENUM('pv','fav','cart','buy') NOT NULL  COMMENT '行为类型：pv浏览/fav收藏/cart加购/buy购买',
  ts            INT UNSIGNED    NOT NULL                COMMENT 'Unix 时间戳（秒，UTC）',
  dt            DATETIME        NOT NULL                COMMENT '北京时间（= ts + 8 小时）',
  session_id    BIGINT UNSIGNED NOT NULL                COMMENT '会话 ID：同一用户相邻行为间隔 >30 分钟即切分',
  PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='淘宝用户行为日志·短窗口用户级抽样+完整轨迹+会话切分';

-- ----------------------------------------------------------------------------
-- 元数据表：把数据来源与处理参数写进库里，便于复现与写报告
--   由 load 脚本读取 output/build_report.json 自动填充
-- ----------------------------------------------------------------------------
DROP TABLE IF EXISTS dataset_meta;
CREATE TABLE dataset_meta (
  k VARCHAR(64)  NOT NULL COMMENT '参数名',
  v VARCHAR(512) NOT NULL COMMENT '参数值',
  PRIMARY KEY (k)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='数据集来源与处理参数';
