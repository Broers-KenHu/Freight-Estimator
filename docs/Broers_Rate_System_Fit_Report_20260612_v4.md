# Broers Hunter SYD Application Report

生成日期：2026-06-12

## 1. Hunter SYD 与现有 2025 的差别

差别不是地址或 zone 缺失，而是价格不同。

| 项目 | 结果 |
|---|---:|
| Broers Hunter SYD 可匹配地址行 | 16,549 |
| 现有 Hunter Sydney 2025 地址行 | 16,549 |
| Missing zone map | 0 |
| Missing rate | 0 |
| 价格完全相同的行 | 0 |
| 价格不同的行 | 16,549 |

样例：

| Destination | 原 Hunter Sydney 2025 | Broers Hunter SYD |
|---|---|---|
| `ABBOTSBURY NSW 2176` | min 21.00, basic 8.93, per kg 0.17 | min 21.00, basic 9.6390, per kg 0.2100 |
| `ABERDARE NSW 2325` | min 20.00, basic 13.38, per kg 0.54 | min 20.00, basic 14.7800, per kg 0.5800 |

## 2. 已执行动作

按用户确认：如果不同，应以 HUNTER SYD 为准。

已将现有 `SP-HUNTER-SYD-2025` rate card 覆盖为 Broers Hunter SYD 20240920。

没有新增 rate card。  
没有新增 carrier/service/channel。  
Quote channel 仍沿用 `pc_hunter_syd_2025`，避免前端和 audit 配置变更。

覆盖结果：

| 项目 | 数量 |
|---|---:|
| Updated `RateZone` rows | 16,549 |
| Rebuilt `RateRule` rows | 97 |
| Missing zone map | 0 |
| Missing rate | 0 |

## 3. 覆盖后验证

覆盖后再次全量对比 Broers Hunter SYD：

| 项目 | 数量 |
|---|---:|
| Rows checked | 16,549 |
| Same rows | 16,549 |
| Different rows | 0 |
| Missing zone | 0 |
| Missing rate | 0 |

抽样：

`ABBOTSBURY NSW 2176` 当前系统 `SP-HUNTER-SYD-2025` 已显示 Broers Hunter SYD：

```text
basic: 9.6390
per_kg: 0.21
minimum: 21.0000
source_to_zone: SYDNEY
source_to_zone_number: 62
```

## 4. 备份与追踪

覆盖前旧数据已备份：

```text
outputs\broers_rate_analysis\hunter_sydney_before_broers_apply_20260612_014150.json
```

新增管理命令：

```text
backend\freight\management\commands\apply_broers_hunter_sydney_rates.py
```

覆盖后验证文件：

```text
outputs\broers_rate_analysis\hunter_syd_after_apply_validation.json
```

## 5. 当前系统事实

- `pc_hunter_mel_2023`：仍使用 PostageCalculator Hunter MEL 2023，且与 Broers Hunter MEL 无差异。
- `pc_hunter_syd_2025`：channel 名保留，但底层 `SP-HUNTER-SYD-2025` 已使用 Broers Hunter SYD 20240920。
- Hunter surcharge/fuel 规则未变。

