// API实体字段约定：来源{id,natural_key,data}、口径{id,version,data}、报告{id,status,snapshot,checksum,result,approved}、计划{next_at,enabled}、账单{bill_no,revision,request_ref,minutes,occupied_by}、核对会话{id,name,run_id,generation,counts,occupied}。
export const ENTITY_KEYS=['sources','policies','runs','schedules','deliveries','bills','reconSessions'];
