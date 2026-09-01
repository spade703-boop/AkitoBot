# M2 事件记忆离线召回报告

> 该报告评估事件候选召回和安全拒绝，不代表线上模型回复质量；原作事件是证据锚点，不是逐字答案。
>
> 这是生成的评测快照；路线和未完成项见 [`REFACTOR_PLAN.md`](REFACTOR_PLAN.md)。

- 事件总数：826
- 高置信度事件：611
- 评测样例：44（正例 27 / 负例 10 / 模糊例 7）
- Recall@1：0.8519
- Recall@3：1.0
- MRR：0.9259
- 负例误认率：0.0
- 负例特异度：1.0
- 模糊问法拒绝率：1.0

| Case | Kind | Result | Reason | Top / second | Retrieved event ids |
| --- | --- | --- | --- | --- | --- |
| positive-001 | positive | pass | - | 9.723 / 8.203 | akito-toya-2641644bcc5a, akito-toya-14dcc9ac3317, akito-toya-3bf862348631 |
| positive-002 | positive | pass | - | 9.723 / 5.953 | akito-toya-fa8f8e1b9ce8 |
| positive-003 | positive | pass | - | 6.02 / 2.25 | akito-toya-ee59c2eeae20 |
| positive-004 | positive | pass | - | 9.723 / 8.843 | akito-toya-b3e2299b1d04, akito-toya-cddce7e7a13c, akito-toya-9167b8b4663b |
| positive-005 | positive | pass | - | 9.06 / 7.54 | akito-toya-4c047c6930c9, akito-toya-d4f27ebfbe78 |
| positive-006 | positive | pass | - | 7.395 / 4.312 | akito-toya-6d813d3cb93b |
| positive-007 | positive | pass | - | 9.723 / 9.723 | akito-toya-064c80621ed1, akito-toya-0fae8a16bbcb, akito-toya-14dcc9ac3317 |
| positive-008 | positive | pass | - | 6.02 / 6.02 | akito-toya-949769a4f68f, akito-toya-9b4818230f76 |
| positive-009 | positive | pass | - | 9.06 / 6.02 | akito-toya-9463136cc3a9 |
| positive-010 | positive | pass | - | 7.395 / 6.02 | akito-toya-959acbeb4b88, akito-toya-5c318eaa5c62, akito-toya-743d82967cef |
| positive-011 | positive | pass | - | 9.723 / 9.723 | akito-toya-14dcc9ac3317, akito-toya-89dd6802fb96, akito-toya-9cdd5216b88f |
| positive-012 | positive | pass | - | 6.043 / 6.02 | akito-toya-web-b08adb36a69c, akito-toya-000fec0ca961 |
| positive-013 | positive | pass | - | 4.5 / 4.5 | akito-toya-00ffaac0ca8c, akito-toya-0525faeab362, akito-toya-51c58c963e3f |
| positive-014 | positive | pass | - | 7.54 / 7.54 | akito-toya-0199316b03f3, akito-toya-89f69261da22, akito-toya-471c6a016efc |
| positive-015 | positive | pass | - | 9.06 / 6.02 | akito-toya-89f69261da22 |
| positive-016 | positive | pass | - | 6.02 / 4.5 | akito-toya-c3988e29145e, akito-toya-2c2ef4a60384, akito-toya-e6ff90f2c432 |
| positive-017 | positive | pass | - | 9.51 / 9.51 | akito-toya-02700b74e5bb, akito-toya-57cc9b994a80, akito-toya-46e5e13337a9 |
| positive-018 | positive | pass | - | 6.02 / 6.02 | akito-toya-51b9b9d8f97d, akito-toya-9bb64bbb190e, akito-toya-web-b08adb36a69c |
| positive-019 | positive | pass | - | 7.54 / 6.02 | akito-toya-39863d7fc083, akito-toya-55866d154e4d, akito-toya-6f30c8063b9c |
| positive-020 | positive | pass | - | 6.47 / 6.47 | akito-toya-a8209589483e, akito-toya-e0403691bf32, akito-toya-32f12f8a3f62 |
| positive-021 | positive | pass | - | 7.395 / 7.395 | akito-toya-375bc1a91e65, akito-toya-77a795ce3810, akito-toya-b535afbd2b21 |
| positive-022 | positive | pass | - | 10.197 / 9.365 | akito-toya-5718f6364bf8, akito-toya-a1143014ba0e |
| positive-023 | positive | pass | - | 9.51 / 7.99 | akito-toya-02d0bd53c469, akito-toya-0558503ae582, akito-toya-310fb75adeed |
| positive-024 | positive | pass | - | 7.54 / 6.02 | akito-toya-91c59120c695, akito-toya-06ed04c2c5a6, akito-toya-d2bb3c6bfbc3 |
| positive-025 | positive | pass | - | 11.098 / 9.723 | akito-toya-89dd6802fb96, akito-toya-14dcc9ac3317, akito-toya-9167b8b4663b |
| positive-026 | positive | pass | - | 7.54 / 6.02 | akito-toya-7f3f46b54843, akito-toya-83cf299c4c6c |
| positive-027 | positive | pass | - | 4.84 / 2.25 | akito-toya-web-30b154922d2f |
| negative-001 | negative | pass | low_score | 2.25 / 2.25 | （无） |
| negative-002 | negative | pass | low_score | 2.25 / 0.8 | （无） |
| negative-003 | negative | pass | low_score | 2.25 / 2.25 | （无） |
| negative-004 | negative | pass | low_score | 2.25 / 2.25 | （无） |
| negative-005 | negative | pass | ambiguous_candidates | 3.77 / 3.77 | （无） |
| negative-006 | negative | pass | ambiguous_candidates | 4.22 / 4.22 | （无） |
| negative-007 | negative | pass | low_score | 2.25 / 0.0 | （无） |
| negative-008 | negative | pass | ambiguous_candidates | 3.77 / 3.77 | （无） |
| negative-009 | negative | pass | low_score | 2.25 / 2.25 | （无） |
| negative-010 | negative | pass | low_score | 2.25 / 2.25 | （无） |
| ambiguous-001 | ambiguous | pass | insufficient_event_cues | 0.0 / 0.0 | （无） |
| ambiguous-002 | ambiguous | pass | insufficient_event_cues | 0.0 / 0.0 | （无） |
| ambiguous-003 | ambiguous | pass | ambiguous_candidates | 3.77 / 3.77 | （无） |
| ambiguous-004 | ambiguous | pass | insufficient_event_cues | 0.0 / 0.0 | （无） |
| ambiguous-005 | ambiguous | pass | insufficient_event_cues | 0.0 / 0.0 | （无） |
| ambiguous-006 | ambiguous | pass | insufficient_event_cues | 0.0 / 0.0 | （无） |
| ambiguous-007 | ambiguous | pass | insufficient_event_cues | 0.0 / 0.0 | （无） |
