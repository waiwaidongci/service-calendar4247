# 002 账单核对

在001基础上新增对账表，均由SQLAlchemy启动时create_all创建；存量库升级前先备份。

- bills：结算方回传账单，(bill_no, revision)唯一；同一账单号多修订并存，当前值取最高修订号。
- bill_conflicts：同版异值隔离区，仅记录不参与计算。
- bill_batches：导入批次计数（收到/入库/重复忽略/旧版忽略/隔离），整批一个事务。
- recon_sessions：核对会话，name唯一，generation为占用代数（每次发布+1，用于拒绝迟到计算）。
- bill_claims：账单占用，bill_no全局唯一（跨报告不可重复占用），每张账单只归一条请求。
- recon_diffs：逐笔差异（漏单/多单/分钟差/吻合），随占用同事务原子发布。

sla_runs.snapshot自本次起增加source_keys（底稿稳定编号，与sources按位对齐）；不含该字段的旧快照不能用于对账，需重新排队核算。报告与交付表结构不变，对账过程只读不写。
