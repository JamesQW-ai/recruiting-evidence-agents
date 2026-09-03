# Base 接入验收矩阵

## 用途

本矩阵是用户选定的模拟 `候选人流程主表` 的验收契约，供 `base-material-pack-agent` 使用。该表包含 12 条记录和 29 个业务字段，其中 `处理状态` 是受控处理状态字段。本契约不记录 Base URL、令牌、附件令牌、本地 Profile 或收件人。

Base 仍是可协作编辑的材料提交和流程事实来源。本矩阵仅记录只读抓取的预期结果，不向 Base 新增异常、推荐或决策字段。

## 运行边界

使用明确授权的 Base URL 和两个空的本地目录运行 `base-material-pack-agent`。本地快照是回放控制件。运行 `candidate-pack-agent` 前，交接目录只允许包含 `materials.zip`、`candidate_process_register.xlsx`、`run_control.json`、`base_intake_observations.json`，以及仅在 `export-resolution` 已验证时出现的临时 `follow_up_resolution.json`；`run_control.json` 必须绑定 ZIP、登记册、Base 观察、源指纹和插件 manifest 的 SHA-256/字节数，Candidate Pack 在读取业务数据前逐项复算。目录、符号链接、其他文件或任一摘要不匹配均为阻断，并要求从授权源新建运行。存在未到达边界的材料问题时只保留 `operation_log.jsonl`，成功完成后最终目录为五个正式交付物加该日志。

`materials.zip` 在 `input/` 根目录保留一份共享技术题目，并将可唯一归属的候选人材料直接放入 `input/<candidate>/<filename>`。没有任何可唯一归属材料的候选人不创建候选人目录。每位候选人的 ZIP 目录不再包含来源序号等子目录；同一候选人出现同名文件时，后续副本以稳定的 `__source-0002` 后缀区分，源字节与快照中的原始文件名仍保留。

## 预期场景

| Candidate ID | 已记录的场景 | 预期 ZIP 结果 | 预期结论 |
| --- | --- | --- | --- |
| `S1-KN-001` | 全流程已记录；张三字段中的两份技术作业评估，内容唯一指向李四。 | `input/张三/` 包含唯一归属张三的材料；两次错放的评估操作以原始文件名进入 `input/李四/`。 | 内容身份优先于 Base 行和文件名。成功重归属不构成 `Unmapped Material`；相同的提交操作参与重复检测。 |
| `S1-KN-002` | 技术作业评估为 `Not Provided`，后续阶段为 `Not Reached`。 | `input/李四/` 包含简历，以及从张三字段中唯一归属到李四的错放评估字节。 | 不从 `Not Reached` 推断缺少后续材料。重归属后的评估不得以原所在行替代候选人身份。 |
| `S1-KN-003` | 技术面试通过，BP 前主动撤回。 | `input/王五/` 包含已有的简历、评估和技术面试材料。 | 保留 `Withdrawn`，不要求 BP 或 HRD 材料。 |
| `S1-KN-004` | 技术面试前已停止。 | `input/吴晨/` 包含已有的简历和技术作业评估。 | 保留 `Stopped`，不要求面试材料。 |
| `S1-KN-005` | 已发生技术面试后停止。 | `input/林妍/` 包含已有的简历、评估和技术面试材料。 | 不要求 BP 或 HRD 材料。 |
| `S1-KN-006` | 已发生 BP 面试后停止。 | `input/周启明/` 包含已有的简历、评估、技术面试和 BP 面试材料。 | 不要求 HRD 材料。 |
| `S1-KN-007` | 技术面试字段在有效材料外混入一份无法唯一归属的通用文件。 | `input/陈晓彤/` 仅包含可唯一归属的材料；通用文件不进入任何候选人目录。 | 对该通用文件生成一项 `Unmapped Material`；不补造或推断其归属。 |
| `S1-KN-008` | 技术面试前 `Decision Blocked`。 | `input/赵子涵/` 包含已有的简历和技术作业评估。 | `Decision Blocked` 是源记录中的状态，不是推荐或 CEO 决策；不要求面试材料。 |
| `S1-KN-009` | 技术和 BP 面试后，在 BP 阶段 `Decision Blocked`。 | `input/何嘉宁/` 包含已有的简历、评估、技术面试和 BP 面试材料。 | 不要求 HRD 材料。 |
| `S1-KN-010` | 全流程已记录；BP 转写以相同字节提交两次。 | `input/孙明远/` 在不同源路径保留两次合格的转写操作。 | Candidate Pack 报告 `Duplicate Submission`，不静默删除任一副本。 |
| `S1-KN-011` | 已发生 HRD 面试后 `Decision Blocked`。 | `input/许思涵/` 包含已发生阶段的合格材料。 | 保留 `Decision Blocked`，不生成材料异常。 |
| `S1-KN-012` | 全流程已记录且材料完整。 | `input/杜文博/` 包含所有已发生阶段的合格材料。 | 不应生成材料观察项。 |

## 预期汇总观察项

- `Unmapped Material`：`S1-KN-007` 的通用技术面试文件不进入候选人目录。
- `Duplicate Submission`：Candidate Pack 基于 ZIP 哈希识别跨候选人重复的评估操作，以及 `S1-KN-010` 两份相同的 BP 转写。全部合格副本留在 ZIP 中。
- `Not Reached`、`Stopped`、`Withdrawn`、`Decision Blocked` 与 `Not Provided` 均是流程事实。除非已记录的材料事件缺少可唯一归属的附件，否则它们不是材料异常。
- 当前 12 人基准没有“流程已发生且全部材料字段为空”的专用场景；在用户明确指定要调整哪一名既有候选人的流程事实与材料前，不将该场景写入 Base 或作为线上验收结论。

## 验证

Base 输入和观察项结构的离线契约断言位于 `tests/test_base_materials.py`，运行：

```bash
python3 -B -m unittest discover -s tests -v
```

进行线上验收回放时，使用新的空快照和交接目录。在调用 `candidate-pack-agent` 前，将临时的 `base_intake_observations.json` 与本矩阵比对；不得将快照保留在最终交付目录中。

## 最近已核验的只读回放

当前合成 Base 回放结果如下：

- 读取 12 条记录、79 次附件操作，生成 69 个本地快照文件；Base 未被写入。
- 最终观察项共 3 项：两组 `Duplicate Submission`（李四的两次错放评估、孙明远的两份 BP 转写）和一项 `Unmapped Material`（陈晓彤技术面试字段中的通用文件）。
- 最终目录严格只有五个正式文件和 `operation_log.jsonl`；`unzip -t` 通过，临时登记表、`run_control.json` 和 Base 观察文件已删除。
- 此回放是自动化证据，不构成 `Evidence Freeze`。
