# M2 事件记忆离线召回报告

> 该报告评估事件候选召回和安全拒绝，不代表线上模型回复质量；原作事件是证据锚点，不是逐字答案。

- 事件总数：824
- 高置信度事件：609
- 评测样例：43（正例 26 / 负例 10 / 模糊例 7）
- Recall@1：0.8077
- Recall@3：1.0
- MRR：0.9038
- 负例误认率：0.0
- 负例特异度：1.0
- 模糊问法拒绝率：1.0

| Case | Kind | Result | Reason | Top / second | Retrieved event ids |
| --- | --- | --- | --- | --- | --- |
| positive-001 | positive | pass | - | 11.0 / 11.0 | akito-toya-2641644bcc5a, akito-toya-3059d71da1a9, akito-toya-4b64bf431a37 |
| positive-002 | positive | pass | - | 8.0 / 6.0 | akito-toya-fa8f8e1b9ce8, akito-toya-9167b8b4663b, akito-toya-f02a918cd361 |
| positive-003 | positive | pass | - | 3.0 / 0.5 | akito-toya-ee59c2eeae20, akito-toya-0e51bd03d3e0, akito-toya-0fae8a16bbcb |
| positive-004 | positive | pass | - | 9.0 / 7.0 | akito-toya-b3e2299b1d04, akito-toya-9167b8b4663b, akito-toya-064c80621ed1 |
| positive-005 | positive | pass | - | 11.0 / 5.0 | akito-toya-4c047c6930c9, akito-toya-d4f27ebfbe78, akito-toya-8a823b8021ba |
| positive-006 | positive | pass | - | 4.0 / 1.5 | akito-toya-6d813d3cb93b, akito-toya-f245c67bda78, akito-toya-085de116b15d |
| positive-007 | positive | pass | - | 11.0 / 9.0 | akito-toya-cfb07a926f19, akito-toya-064c80621ed1, akito-toya-0fae8a16bbcb |
| positive-008 | positive | pass | - | 6.5 / 6.5 | akito-toya-949769a4f68f, akito-toya-9b4818230f76, akito-toya-cfda6c00b8ce |
| positive-009 | positive | pass | - | 10.5 / 8.5 | akito-toya-9463136cc3a9, akito-toya-a13830a3cd15, akito-toya-018bad8b8b69 |
| positive-010 | positive | pass | - | 5.5 / 4.5 | akito-toya-959acbeb4b88, akito-toya-5c318eaa5c62, akito-toya-743d82967cef |
| positive-011 | positive | pass | - | 9.5 / 7.5 | akito-toya-14dcc9ac3317, akito-toya-89dd6802fb96, akito-toya-9cdd5216b88f |
| positive-012 | positive | pass | - | 4.5 / 4.0 | akito-toya-000fec0ca961, akito-toya-363e87d3047e, akito-toya-67b977f2751d |
| positive-013 | positive | pass | - | 4.5 / 4.5 | akito-toya-00ffaac0ca8c, akito-toya-0525faeab362, akito-toya-51c58c963e3f |
| positive-014 | positive | pass | - | 6.5 / 4.5 | akito-toya-0199316b03f3, akito-toya-471c6a016efc, akito-toya-89f69261da22 |
| positive-015 | positive | pass | - | 6.5 / 5.0 | akito-toya-89f69261da22, akito-toya-471c6a016efc, akito-toya-956466e88966 |
| positive-016 | positive | pass | - | 4.5 / 4.0 | akito-toya-c3988e29145e, akito-toya-307682a08e23, akito-toya-42119d351493 |
| positive-017 | positive | pass | - | 5.0 / 5.0 | akito-toya-02700b74e5bb, akito-toya-57cc9b994a80, akito-toya-6835ebe79ef8 |
| positive-018 | positive | pass | - | 4.5 / 4.5 | akito-toya-51b9b9d8f97d, akito-toya-9bb64bbb190e, akito-toya-2ba6f93fe10e |
| positive-019 | positive | pass | - | 6.5 / 4.0 | akito-toya-39863d7fc083, akito-toya-29e1ad9ed00a, akito-toya-5c318eaa5c62 |
| positive-020 | positive | pass | - | 5.0 / 5.0 | akito-toya-a8209589483e, akito-toya-e0403691bf32, akito-toya-5f5f08a087ee |
| positive-021 | positive | pass | - | 5.5 / 5.0 | akito-toya-f245c67bda78, akito-toya-375bc1a91e65, akito-toya-3eeb123976c5 |
| positive-022 | positive | pass | - | 6.0 / 5.5 | akito-toya-1c58bf429007, akito-toya-5718f6364bf8, akito-toya-a1143014ba0e |
| positive-023 | positive | pass | - | 5.5 / 4.5 | akito-toya-02d0bd53c469, akito-toya-385eac2103f9, akito-toya-83cf299c4c6c |
| positive-024 | positive | pass | - | 6.5 / 4.5 | akito-toya-91c59120c695, akito-toya-06ed04c2c5a6, akito-toya-8a488a3e77d7 |
| positive-025 | positive | pass | - | 11.5 / 10.0 | akito-toya-89dd6802fb96, akito-toya-14dcc9ac3317, akito-toya-9167b8b4663b |
| positive-026 | positive | pass | - | 6.5 / 5.0 | akito-toya-7f3f46b54843, akito-toya-83cf299c4c6c, akito-toya-1b10b6c88797 |
| negative-001 | negative | pass | ambiguous_candidates | 3.0 / 3.0 | （无） |
| negative-002 | negative | pass | low_score | 2.5 / 1.5 | （无） |
| negative-003 | negative | pass | low_score | 1.5 / 0.5 | （无） |
| negative-004 | negative | pass | low_score | 2.0 / 2.0 | （无） |
| negative-005 | negative | pass | ambiguous_candidates | 3.0 / 3.0 | （无） |
| negative-006 | negative | pass | ambiguous_candidates | 3.5 / 3.5 | （无） |
| negative-007 | negative | pass | low_score | 0.5 / 0.0 | （无） |
| negative-008 | negative | pass | ambiguous_candidates | 4.0 / 4.0 | （无） |
| negative-009 | negative | pass | low_score | 2.5 / 2.5 | （无） |
| negative-010 | negative | pass | low_score | 1.5 / 1.5 | （无） |
| ambiguous-001 | ambiguous | pass | insufficient_event_cues | 0.0 / 0.0 | （无） |
| ambiguous-002 | ambiguous | pass | insufficient_event_cues | 0.0 / 0.0 | （无） |
| ambiguous-003 | ambiguous | pass | low_score | 2.5 / 2.5 | （无） |
| ambiguous-004 | ambiguous | pass | insufficient_event_cues | 0.0 / 0.0 | （无） |
| ambiguous-005 | ambiguous | pass | insufficient_event_cues | 0.0 / 0.0 | （无） |
| ambiguous-006 | ambiguous | pass | insufficient_event_cues | 0.0 / 0.0 | （无） |
| ambiguous-007 | ambiguous | pass | insufficient_event_cues | 0.0 / 0.0 | （无） |
