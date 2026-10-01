# LLM 请求预算

所有 chat/Responses 网络尝试（包括 HTTP 重试和空响应重试）在传输前原子预留
一次调用及输入/输出 token 额度。父 WorkBudget 同时预留 llm_calls/llm_tokens，
并发兄弟预算共享锁，任一资源不足不扣减其他资源。自动 HTTP redirect 被拒绝，
避免隐藏的未计数请求及凭据转发。

输入额度采用请求 JSON 的 UTF-8 字节数 + 1024 framing 余量；加上输出 token
上限（包含 provider 在输出额度内计算的 reasoning token）。这是一种保守预留，
不是所有 provider/tokenizer 的数学上界。合法 usage 结算后返还未用 token；
缺失、非法 usage、超时或无法判定是否计费的错误保留额度，调用次数不返还。
如 provider 报告实际 usage 超过预留，记录实际数并使该 client 后续请求失败关闭；
已发生的远端计费无法撤销。对需要 provider 级绝对上限的部署，还需供应商账户限额。

LLMUsage 区分 total_tokens（已知实际值）、reserved_tokens（在途/未知计费）
和 unknown_usage_calls。估算价格默认 unknown/null，移除固定费率和伪称真实费用。
可由操作员为 LLMClient 传入 pricing_registry：key 为精确 base URL（末尾 /）+
model，value 包含 input_per_million 与 output_per_million。所有值需非负且有限，
任何请求计费未知时总费用仍为 unknown。费率只产生估算，不代表供应商账单。

各轮预算继续受 client 生命周期总预算约束；更换父轮次不会使旧请求结算到新轮次。
退避时间受剩余 deadline 限制，截止后不发新请求。
