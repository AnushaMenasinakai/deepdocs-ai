# Phase 15A: deterministic multi-query evaluation

B — DO NOT INTEGRATE

No candidate meets all predeclared benefit/safety gates. Keep production unchanged.

Cached MiniLM / in-memory Qdrant; socket connections blocked. Original 35-case benchmark and separate 10-case safety set. No Gemini or production integration.

## Predeclared criteria

{"maximum_added_non_required": 35, "maximum_average_queries": 2, "maximum_context_characters": 12000, "maximum_false_positives": 2, "maximum_queries": 4, "minimum_complete_evidence": 16, "minimum_multi_complete": 4, "minimum_precision": 0.875, "minimum_recovered_false_negatives": 2, "no_new_safety_acceptances": true, "no_new_unsupported_acceptances": true, "no_ranking_regression": true}

## Original benchmark comparison

| Strategy / fusion / gate | Hit@1/3/5 | MRR@5 | TP/TN/FP/FN | P/R/F1 | Complete /22 | Multi /5 | Calls (multiplier) | Added non-required | Avg/max context chunks | Max chars |
|---|---|---|---|---|---|---|---|---|---|---|
| original/max/original | 0.95455/1.00000/1.00000 | 0.97727 | 14/11/2/8 | 0.87500/0.63636/0.73684 | 14 | 4 | 35 (1.000x) | 0 | 3.938/5 | 1725 |
| clauses/max/original | 0.95455/1.00000/1.00000 | 0.97727 | 14/11/2/8 | 0.87500/0.63636/0.73684 | 14 | 4 | 35 (1.000x) | 0 | 3.938/5 | 1725 |
| clauses/max/or | 0.95455/1.00000/1.00000 | 0.97727 | 14/11/2/8 | 0.87500/0.63636/0.73684 | 14 | 4 | 35 (1.000x) | 0 | 3.938/5 | 1725 |
| clauses/rrf/original | 0.95455/1.00000/1.00000 | 0.97727 | 14/11/2/8 | 0.87500/0.63636/0.73684 | 14 | 4 | 35 (1.000x) | 0 | 3.938/5 | 1725 |
| clauses/rrf/or | 0.95455/1.00000/1.00000 | 0.97727 | 14/11/2/8 | 0.87500/0.63636/0.73684 | 14 | 4 | 35 (1.000x) | 0 | 3.938/5 | 1725 |
| topics/max/original | 0.90909/1.00000/1.00000 | 0.95455 | 14/11/2/8 | 0.87500/0.63636/0.73684 | 14 | 4 | 37 (1.057x) | 0 | 3.938/5 | 1725 |
| topics/max/or | 0.90909/1.00000/1.00000 | 0.95455 | 14/11/2/8 | 0.87500/0.63636/0.73684 | 14 | 4 | 37 (1.057x) | 0 | 3.938/5 | 1725 |
| topics/rrf/original | 0.95455/1.00000/1.00000 | 0.97727 | 14/11/2/8 | 0.87500/0.63636/0.73684 | 14 | 4 | 37 (1.057x) | 0 | 3.938/5 | 1725 |
| topics/rrf/or | 0.95455/1.00000/1.00000 | 0.97727 | 14/11/2/8 | 0.87500/0.63636/0.73684 | 14 | 4 | 37 (1.057x) | 0 | 3.938/5 | 1725 |
| combined/max/original | 0.90909/1.00000/1.00000 | 0.95455 | 14/11/2/8 | 0.87500/0.63636/0.73684 | 14 | 4 | 37 (1.057x) | 0 | 3.938/5 | 1725 |
| combined/max/or | 0.90909/1.00000/1.00000 | 0.95455 | 14/11/2/8 | 0.87500/0.63636/0.73684 | 14 | 4 | 37 (1.057x) | 0 | 3.938/5 | 1725 |
| combined/rrf/original | 0.95455/1.00000/1.00000 | 0.97727 | 14/11/2/8 | 0.87500/0.63636/0.73684 | 14 | 4 | 37 (1.057x) | 0 | 3.938/5 | 1725 |
| combined/rrf/or | 0.95455/1.00000/1.00000 | 0.97727 | 14/11/2/8 | 0.87500/0.63636/0.73684 | 14 | 4 | 37 (1.057x) | 0 | 3.938/5 | 1725 |

Ranking metrics use answerable cases only; a hit matches any expected document/page. MRR is truncated at five. Complete evidence requires ALL expected pages. Similarity is not confidence. All candidates cap merged evidence at five, as well as five per query; RRF is a separate ranking score, never compared with 0.50/0.30.

## Eight baseline false negatives

### direct-password (direct)

Which algorithm stores password hashes?

Required: [{'document': 'authentication-guide.pdf', 'page': 3}]

Original top cosine 0.44279455; primary rejects. All required pages already in top five: True.

Top five: authentication-guide.pdf p3 (0.44279455); data-guide.pdf p1 (0.28860458); data-guide.pdf p2 (0.25276707); operations-guide.pdf p1 (0.17411221); storage-guide.pdf p1 (0.16049525)

| Candidate | Queries and top cosine | Gate | Recovered required pages | Added non-required |
|---|---|---|---|---|
| clauses/max/original | Which algorithm stores password hashes? (0.44279455) | False | [] | [] |
| clauses/max/or | Which algorithm stores password hashes? (0.44279455) | False | [] | [] |
| clauses/rrf/original | Which algorithm stores password hashes? (0.44279455) | False | [] | [] |
| clauses/rrf/or | Which algorithm stores password hashes? (0.44279455) | False | [] | [] |
| topics/max/original | Which algorithm stores password hashes? (0.44279455) | False | [] | [] |
| topics/max/or | Which algorithm stores password hashes? (0.44279455) | False | [] | [] |
| topics/rrf/original | Which algorithm stores password hashes? (0.44279455) | False | [] | [] |
| topics/rrf/or | Which algorithm stores password hashes? (0.44279455) | False | [] | [] |
| combined/max/original | Which algorithm stores password hashes? (0.44279455) | False | [] | [] |
| combined/max/or | Which algorithm stores password hashes? (0.44279455) | False | [] | [] |
| combined/rrf/original | Which algorithm stores password hashes? (0.44279455) | False | [] | [] |
| combined/rrf/or | Which algorithm stores password hashes? (0.44279455) | False | [] | [] |
### para-signature (paraphrase)

What stops someone editing a login token and passing it off as genuine?

Required: [{'document': 'authentication-guide.pdf', 'page': 1}]

Original top cosine 0.41665697; primary rejects. All required pages already in top five: True.

Top five: authentication-guide.pdf p1 (0.41665697); web-guide.pdf p1 (0.40223454); authentication-guide.pdf p2 (0.39186361); operations-guide.pdf p3 (0.34184004); authentication-guide.pdf p3 (0.30985787)

| Candidate | Queries and top cosine | Gate | Recovered required pages | Added non-required |
|---|---|---|---|---|
| clauses/max/original | What stops someone editing a login token and passing it off as genuine? (0.41665697) | False | [] | [] |
| clauses/max/or | What stops someone editing a login token and passing it off as genuine? (0.41665697) | False | [] | [] |
| clauses/rrf/original | What stops someone editing a login token and passing it off as genuine? (0.41665697) | False | [] | [] |
| clauses/rrf/or | What stops someone editing a login token and passing it off as genuine? (0.41665697) | False | [] | [] |
| topics/max/original | What stops someone editing a login token and passing it off as genuine? (0.41665697) | False | [] | [] |
| topics/max/or | What stops someone editing a login token and passing it off as genuine? (0.41665697) | False | [] | [] |
| topics/rrf/original | What stops someone editing a login token and passing it off as genuine? (0.41665697) | False | [] | [] |
| topics/rrf/or | What stops someone editing a login token and passing it off as genuine? (0.41665697) | False | [] | [] |
| combined/max/original | What stops someone editing a login token and passing it off as genuine? (0.41665697) | False | [] | [] |
| combined/max/or | What stops someone editing a login token and passing it off as genuine? (0.41665697) | False | [] | [] |
| combined/rrf/original | What stops someone editing a login token and passing it off as genuine? (0.41665697) | False | [] | [] |
| combined/rrf/or | What stops someone editing a login token and passing it off as genuine? (0.41665697) | False | [] | [] |
### para-owner (paraphrase)

Can a different account discover my Knowledge Base by guessing its identifier?

Required: [{'document': 'authentication-guide.pdf', 'page': 4}]

Original top cosine 0.34289047; primary rejects. All required pages already in top five: True.

Top five: authentication-guide.pdf p4 (0.34289047); authentication-guide.pdf p3 (0.27867466); data-guide.pdf p1 (0.19856192); operations-guide.pdf p3 (0.17556605); data-guide.pdf p2 (0.17418029)

| Candidate | Queries and top cosine | Gate | Recovered required pages | Added non-required |
|---|---|---|---|---|
| clauses/max/original | Can a different account discover my Knowledge Base by guessing its identifier? (0.34289047) | False | [] | [] |
| clauses/max/or | Can a different account discover my Knowledge Base by guessing its identifier? (0.34289047) | False | [] | [] |
| clauses/rrf/original | Can a different account discover my Knowledge Base by guessing its identifier? (0.34289047) | False | [] | [] |
| clauses/rrf/or | Can a different account discover my Knowledge Base by guessing its identifier? (0.34289047) | False | [] | [] |
| topics/max/original | Can a different account discover my Knowledge Base by guessing its identifier? (0.34289047) | False | [] | [] |
| topics/max/or | Can a different account discover my Knowledge Base by guessing its identifier? (0.34289047) | False | [] | [] |
| topics/rrf/original | Can a different account discover my Knowledge Base by guessing its identifier? (0.34289047) | False | [] | [] |
| topics/rrf/or | Can a different account discover my Knowledge Base by guessing its identifier? (0.34289047) | False | [] | [] |
| combined/max/original | Can a different account discover my Knowledge Base by guessing its identifier? (0.34289047) | False | [] | [] |
| combined/max/or | Can a different account discover my Knowledge Base by guessing its identifier? (0.34289047) | False | [] | [] |
| combined/rrf/original | Can a different account discover my Knowledge Base by guessing its identifier? (0.34289047) | False | [] | [] |
| combined/rrf/or | Can a different account discover my Knowledge Base by guessing its identifier? (0.34289047) | False | [] | [] |
### para-ocr (paraphrase)

Can the system read words from a scan that contains pictures but no selectable text?

Required: [{'document': 'storage-guide.pdf', 'page': 2}]

Original top cosine 0.48013180; primary rejects. All required pages already in top five: True.

Top five: storage-guide.pdf p2 (0.48013180); data-guide.pdf p2 (0.23136444); storage-guide.pdf p3 (0.19077710); data-guide.pdf p1 (0.15502933); web-guide.pdf p3 (0.13416093)

| Candidate | Queries and top cosine | Gate | Recovered required pages | Added non-required |
|---|---|---|---|---|
| clauses/max/original | Can the system read words from a scan that contains pictures but no selectable text? (0.48013180) | False | [] | [] |
| clauses/max/or | Can the system read words from a scan that contains pictures but no selectable text? (0.48013180) | False | [] | [] |
| clauses/rrf/original | Can the system read words from a scan that contains pictures but no selectable text? (0.48013180) | False | [] | [] |
| clauses/rrf/or | Can the system read words from a scan that contains pictures but no selectable text? (0.48013180) | False | [] | [] |
| topics/max/original | Can the system read words from a scan that contains pictures but no selectable text? (0.48013180) | False | [] | [] |
| topics/max/or | Can the system read words from a scan that contains pictures but no selectable text? (0.48013180) | False | [] | [] |
| topics/rrf/original | Can the system read words from a scan that contains pictures but no selectable text? (0.48013180) | False | [] | [] |
| topics/rrf/or | Can the system read words from a scan that contains pictures but no selectable text? (0.48013180) | False | [] | [] |
| combined/max/original | Can the system read words from a scan that contains pictures but no selectable text? (0.48013180) | False | [] | [] |
| combined/max/or | Can the system read words from a scan that contains pictures but no selectable text? (0.48013180) | False | [] | [] |
| combined/rrf/original | Can the system read words from a scan that contains pictures but no selectable text? (0.48013180) | False | [] | [] |
| combined/rrf/or | Can the system read words from a scan that contains pictures but no selectable text? (0.48013180) | False | [] | [] |
### para-history (paraphrase)

Will an earlier response be remembered automatically when I ask something new?

Required: [{'document': 'data-guide.pdf', 'page': 3}]

Original top cosine 0.36147529; primary rejects. All required pages already in top five: True.

Top five: web-guide.pdf p2 (0.36147529); data-guide.pdf p3 (0.34304364); data-guide.pdf p1 (0.14792566); authentication-guide.pdf p2 (0.14194255); operations-guide.pdf p2 (0.11561224)

| Candidate | Queries and top cosine | Gate | Recovered required pages | Added non-required |
|---|---|---|---|---|
| clauses/max/original | Will an earlier response be remembered automatically when I ask something new? (0.36147529) | False | [] | [] |
| clauses/max/or | Will an earlier response be remembered automatically when I ask something new? (0.36147529) | False | [] | [] |
| clauses/rrf/original | Will an earlier response be remembered automatically when I ask something new? (0.36147529) | False | [] | [] |
| clauses/rrf/or | Will an earlier response be remembered automatically when I ask something new? (0.36147529) | False | [] | [] |
| topics/max/original | Will an earlier response be remembered automatically when I ask something new? (0.36147529) | False | [] | [] |
| topics/max/or | Will an earlier response be remembered automatically when I ask something new? (0.36147529) | False | [] | [] |
| topics/rrf/original | Will an earlier response be remembered automatically when I ask something new? (0.36147529) | False | [] | [] |
| topics/rrf/or | Will an earlier response be remembered automatically when I ask something new? (0.36147529) | False | [] | [] |
| combined/max/original | Will an earlier response be remembered automatically when I ask something new? (0.36147529) | False | [] | [] |
| combined/max/or | Will an earlier response be remembered automatically when I ask something new? (0.36147529) | False | [] | [] |
| combined/rrf/original | Will an earlier response be remembered automatically when I ask something new? (0.36147529) | False | [] | [] |
| combined/rrf/or | Will an earlier response be remembered automatically when I ask something new? (0.36147529) | False | [] | [] |
### multi-cloud (multi_chunk)

Compare what the customer manages in IaaS and PaaS.

Required: [{'document': 'cloud-guide.pdf', 'page': 1}, {'document': 'cloud-guide.pdf', 'page': 2}]

Original top cosine 0.27171542; primary rejects. All required pages already in top five: True.

Top five: cloud-guide.pdf p1 (0.27171542); cloud-guide.pdf p3 (0.26971811); authentication-guide.pdf p3 (0.15951189); operations-guide.pdf p3 (0.11960169); cloud-guide.pdf p2 (0.11700260)

| Candidate | Queries and top cosine | Gate | Recovered required pages | Added non-required |
|---|---|---|---|---|
| clauses/max/original | Compare what the customer manages in IaaS and PaaS. (0.27171542) | False | [] | [] |
| clauses/max/or | Compare what the customer manages in IaaS and PaaS. (0.27171542) | False | [] | [] |
| clauses/rrf/original | Compare what the customer manages in IaaS and PaaS. (0.27171542) | False | [] | [] |
| clauses/rrf/or | Compare what the customer manages in IaaS and PaaS. (0.27171542) | False | [] | [] |
| topics/max/original | Compare what the customer manages in IaaS and PaaS. (0.27171542); Compare what the customer manages in IaaS (0.28087182); Compare what the customer manages in PaaS (0.24494221) | False | [] | [] |
| topics/max/or | Compare what the customer manages in IaaS and PaaS. (0.27171542); Compare what the customer manages in IaaS (0.28087182); Compare what the customer manages in PaaS (0.24494221) | False | [] | [] |
| topics/rrf/original | Compare what the customer manages in IaaS and PaaS. (0.27171542); Compare what the customer manages in IaaS (0.28087182); Compare what the customer manages in PaaS (0.24494221) | False | [] | [] |
| topics/rrf/or | Compare what the customer manages in IaaS and PaaS. (0.27171542); Compare what the customer manages in IaaS (0.28087182); Compare what the customer manages in PaaS (0.24494221) | False | [] | [] |
| combined/max/original | Compare what the customer manages in IaaS and PaaS. (0.27171542); Compare what the customer manages in IaaS (0.28087182); Compare what the customer manages in PaaS (0.24494221) | False | [] | [] |
| combined/max/or | Compare what the customer manages in IaaS and PaaS. (0.27171542); Compare what the customer manages in IaaS (0.28087182); Compare what the customer manages in PaaS (0.24494221) | False | [] | [] |
| combined/rrf/original | Compare what the customer manages in IaaS and PaaS. (0.27171542); Compare what the customer manages in IaaS (0.28087182); Compare what the customer manages in PaaS (0.24494221) | False | [] | [] |
| combined/rrf/or | Compare what the customer manages in IaaS and PaaS. (0.27171542); Compare what the customer manages in IaaS (0.28087182); Compare what the customer manages in PaaS (0.24494221) | False | [] | [] |
### ambiguous-data (ambiguous)

Where is application metadata stored?

Required: [{'document': 'data-guide.pdf', 'page': 1}]

Original top cosine 0.47693742; primary rejects. All required pages already in top five: True.

Top five: data-guide.pdf p1 (0.47693742); storage-guide.pdf p1 (0.35370438); cloud-guide.pdf p2 (0.32550393); operations-guide.pdf p4 (0.30025573); operations-guide.pdf p1 (0.28046075)

| Candidate | Queries and top cosine | Gate | Recovered required pages | Added non-required |
|---|---|---|---|---|
| clauses/max/original | Where is application metadata stored? (0.47693742) | False | [] | [] |
| clauses/max/or | Where is application metadata stored? (0.47693742) | False | [] | [] |
| clauses/rrf/original | Where is application metadata stored? (0.47693742) | False | [] | [] |
| clauses/rrf/or | Where is application metadata stored? (0.47693742) | False | [] | [] |
| topics/max/original | Where is application metadata stored? (0.47693742) | False | [] | [] |
| topics/max/or | Where is application metadata stored? (0.47693742) | False | [] | [] |
| topics/rrf/original | Where is application metadata stored? (0.47693742) | False | [] | [] |
| topics/rrf/or | Where is application metadata stored? (0.47693742) | False | [] | [] |
| combined/max/original | Where is application metadata stored? (0.47693742) | False | [] | [] |
| combined/max/or | Where is application metadata stored? (0.47693742) | False | [] | [] |
| combined/rrf/original | Where is application metadata stored? (0.47693742) | False | [] | [] |
| combined/rrf/or | Where is application metadata stored? (0.47693742) | False | [] | [] |
### ambiguous-remember (ambiguous)

Does the next question use my past answers?

Required: [{'document': 'data-guide.pdf', 'page': 3}]

Original top cosine 0.34939177; primary rejects. All required pages already in top five: True.

Top five: data-guide.pdf p3 (0.34939177); data-guide.pdf p1 (0.14381718); web-guide.pdf p2 (0.13214717); operations-guide.pdf p4 (0.12910506); operations-guide.pdf p1 (0.12112999)

| Candidate | Queries and top cosine | Gate | Recovered required pages | Added non-required |
|---|---|---|---|---|
| clauses/max/original | Does the next question use my past answers? (0.34939177) | False | [] | [] |
| clauses/max/or | Does the next question use my past answers? (0.34939177) | False | [] | [] |
| clauses/rrf/original | Does the next question use my past answers? (0.34939177) | False | [] | [] |
| clauses/rrf/or | Does the next question use my past answers? (0.34939177) | False | [] | [] |
| topics/max/original | Does the next question use my past answers? (0.34939177) | False | [] | [] |
| topics/max/or | Does the next question use my past answers? (0.34939177) | False | [] | [] |
| topics/rrf/original | Does the next question use my past answers? (0.34939177) | False | [] | [] |
| topics/rrf/or | Does the next question use my past answers? (0.34939177) | False | [] | [] |
| combined/max/original | Does the next question use my past answers? (0.34939177) | False | [] | [] |
| combined/max/or | Does the next question use my past answers? (0.34939177) | False | [] | [] |
| combined/rrf/original | Does the next question use my past answers? (0.34939177) | False | [] | [] |
| combined/rrf/or | Does the next question use my past answers? (0.34939177) | False | [] | [] |

## All 13 unsupported original cases

Each candidate is shown; a high cosine is not proof of answer support.

| Case / question | Candidate | Queries / top cosine | Gate | New FP | Opening query indices |
|---|---|---|---|---|---|
| unsupported-refresh: How long are refresh tokens stored? | original/max/original | Q0: How long are refresh tokens stored? (0.57503199) | True | False | [0] |
| unsupported-refresh: How long are refresh tokens stored? | clauses/max/original | Q0: How long are refresh tokens stored? (0.57503199) | True | False | [0] |
| unsupported-refresh: How long are refresh tokens stored? | clauses/max/or | Q0: How long are refresh tokens stored? (0.57503199) | True | False | [0] |
| unsupported-refresh: How long are refresh tokens stored? | clauses/rrf/original | Q0: How long are refresh tokens stored? (0.57503199) | True | False | [0] |
| unsupported-refresh: How long are refresh tokens stored? | clauses/rrf/or | Q0: How long are refresh tokens stored? (0.57503199) | True | False | [0] |
| unsupported-refresh: How long are refresh tokens stored? | topics/max/original | Q0: How long are refresh tokens stored? (0.57503199) | True | False | [0] |
| unsupported-refresh: How long are refresh tokens stored? | topics/max/or | Q0: How long are refresh tokens stored? (0.57503199) | True | False | [0] |
| unsupported-refresh: How long are refresh tokens stored? | topics/rrf/original | Q0: How long are refresh tokens stored? (0.57503199) | True | False | [0] |
| unsupported-refresh: How long are refresh tokens stored? | topics/rrf/or | Q0: How long are refresh tokens stored? (0.57503199) | True | False | [0] |
| unsupported-refresh: How long are refresh tokens stored? | combined/max/original | Q0: How long are refresh tokens stored? (0.57503199) | True | False | [0] |
| unsupported-refresh: How long are refresh tokens stored? | combined/max/or | Q0: How long are refresh tokens stored? (0.57503199) | True | False | [0] |
| unsupported-refresh: How long are refresh tokens stored? | combined/rrf/original | Q0: How long are refresh tokens stored? (0.57503199) | True | False | [0] |
| unsupported-refresh: How long are refresh tokens stored? | combined/rrf/or | Q0: How long are refresh tokens stored? (0.57503199) | True | False | [0] |
| unsupported-rotation: How often does this server rotate its JWT signing secret? | original/max/original | Q0: How often does this server rotate its JWT signing secret? (0.51616776) | True | False | [0] |
| unsupported-rotation: How often does this server rotate its JWT signing secret? | clauses/max/original | Q0: How often does this server rotate its JWT signing secret? (0.51616776) | True | False | [0] |
| unsupported-rotation: How often does this server rotate its JWT signing secret? | clauses/max/or | Q0: How often does this server rotate its JWT signing secret? (0.51616776) | True | False | [0] |
| unsupported-rotation: How often does this server rotate its JWT signing secret? | clauses/rrf/original | Q0: How often does this server rotate its JWT signing secret? (0.51616776) | True | False | [0] |
| unsupported-rotation: How often does this server rotate its JWT signing secret? | clauses/rrf/or | Q0: How often does this server rotate its JWT signing secret? (0.51616776) | True | False | [0] |
| unsupported-rotation: How often does this server rotate its JWT signing secret? | topics/max/original | Q0: How often does this server rotate its JWT signing secret? (0.51616776) | True | False | [0] |
| unsupported-rotation: How often does this server rotate its JWT signing secret? | topics/max/or | Q0: How often does this server rotate its JWT signing secret? (0.51616776) | True | False | [0] |
| unsupported-rotation: How often does this server rotate its JWT signing secret? | topics/rrf/original | Q0: How often does this server rotate its JWT signing secret? (0.51616776) | True | False | [0] |
| unsupported-rotation: How often does this server rotate its JWT signing secret? | topics/rrf/or | Q0: How often does this server rotate its JWT signing secret? (0.51616776) | True | False | [0] |
| unsupported-rotation: How often does this server rotate its JWT signing secret? | combined/max/original | Q0: How often does this server rotate its JWT signing secret? (0.51616776) | True | False | [0] |
| unsupported-rotation: How often does this server rotate its JWT signing secret? | combined/max/or | Q0: How often does this server rotate its JWT signing secret? (0.51616776) | True | False | [0] |
| unsupported-rotation: How often does this server rotate its JWT signing secret? | combined/rrf/original | Q0: How often does this server rotate its JWT signing secret? (0.51616776) | True | False | [0] |
| unsupported-rotation: How often does this server rotate its JWT signing secret? | combined/rrf/or | Q0: How often does this server rotate its JWT signing secret? (0.51616776) | True | False | [0] |
| unsupported-region: Which geographic region hosts the MongoDB cluster? | original/max/original | Q0: Which geographic region hosts the MongoDB cluster? (0.44434452) | False | False | [] |
| unsupported-region: Which geographic region hosts the MongoDB cluster? | clauses/max/original | Q0: Which geographic region hosts the MongoDB cluster? (0.44434452) | False | False | [] |
| unsupported-region: Which geographic region hosts the MongoDB cluster? | clauses/max/or | Q0: Which geographic region hosts the MongoDB cluster? (0.44434452) | False | False | [] |
| unsupported-region: Which geographic region hosts the MongoDB cluster? | clauses/rrf/original | Q0: Which geographic region hosts the MongoDB cluster? (0.44434452) | False | False | [] |
| unsupported-region: Which geographic region hosts the MongoDB cluster? | clauses/rrf/or | Q0: Which geographic region hosts the MongoDB cluster? (0.44434452) | False | False | [] |
| unsupported-region: Which geographic region hosts the MongoDB cluster? | topics/max/original | Q0: Which geographic region hosts the MongoDB cluster? (0.44434452) | False | False | [] |
| unsupported-region: Which geographic region hosts the MongoDB cluster? | topics/max/or | Q0: Which geographic region hosts the MongoDB cluster? (0.44434452) | False | False | [] |
| unsupported-region: Which geographic region hosts the MongoDB cluster? | topics/rrf/original | Q0: Which geographic region hosts the MongoDB cluster? (0.44434452) | False | False | [] |
| unsupported-region: Which geographic region hosts the MongoDB cluster? | topics/rrf/or | Q0: Which geographic region hosts the MongoDB cluster? (0.44434452) | False | False | [] |
| unsupported-region: Which geographic region hosts the MongoDB cluster? | combined/max/original | Q0: Which geographic region hosts the MongoDB cluster? (0.44434452) | False | False | [] |
| unsupported-region: Which geographic region hosts the MongoDB cluster? | combined/max/or | Q0: Which geographic region hosts the MongoDB cluster? (0.44434452) | False | False | [] |
| unsupported-region: Which geographic region hosts the MongoDB cluster? | combined/rrf/original | Q0: Which geographic region hosts the MongoDB cluster? (0.44434452) | False | False | [] |
| unsupported-region: Which geographic region hosts the MongoDB cluster? | combined/rrf/or | Q0: Which geographic region hosts the MongoDB cluster? (0.44434452) | False | False | [] |
| unsupported-price: What monthly price does the PaaS provider charge? | original/max/original | Q0: What monthly price does the PaaS provider charge? (0.18819380) | False | False | [] |
| unsupported-price: What monthly price does the PaaS provider charge? | clauses/max/original | Q0: What monthly price does the PaaS provider charge? (0.18819380) | False | False | [] |
| unsupported-price: What monthly price does the PaaS provider charge? | clauses/max/or | Q0: What monthly price does the PaaS provider charge? (0.18819380) | False | False | [] |
| unsupported-price: What monthly price does the PaaS provider charge? | clauses/rrf/original | Q0: What monthly price does the PaaS provider charge? (0.18819380) | False | False | [] |
| unsupported-price: What monthly price does the PaaS provider charge? | clauses/rrf/or | Q0: What monthly price does the PaaS provider charge? (0.18819380) | False | False | [] |
| unsupported-price: What monthly price does the PaaS provider charge? | topics/max/original | Q0: What monthly price does the PaaS provider charge? (0.18819380) | False | False | [] |
| unsupported-price: What monthly price does the PaaS provider charge? | topics/max/or | Q0: What monthly price does the PaaS provider charge? (0.18819380) | False | False | [] |
| unsupported-price: What monthly price does the PaaS provider charge? | topics/rrf/original | Q0: What monthly price does the PaaS provider charge? (0.18819380) | False | False | [] |
| unsupported-price: What monthly price does the PaaS provider charge? | topics/rrf/or | Q0: What monthly price does the PaaS provider charge? (0.18819380) | False | False | [] |
| unsupported-price: What monthly price does the PaaS provider charge? | combined/max/original | Q0: What monthly price does the PaaS provider charge? (0.18819380) | False | False | [] |
| unsupported-price: What monthly price does the PaaS provider charge? | combined/max/or | Q0: What monthly price does the PaaS provider charge? (0.18819380) | False | False | [] |
| unsupported-price: What monthly price does the PaaS provider charge? | combined/rrf/original | Q0: What monthly price does the PaaS provider charge? (0.18819380) | False | False | [] |
| unsupported-price: What monthly price does the PaaS provider charge? | combined/rrf/or | Q0: What monthly price does the PaaS provider charge? (0.18819380) | False | False | [] |
| unsupported-recovery: What is the measured recovery time after a North outage? | original/max/original | Q0: What is the measured recovery time after a North outage? (0.44002135) | False | False | [] |
| unsupported-recovery: What is the measured recovery time after a North outage? | clauses/max/original | Q0: What is the measured recovery time after a North outage? (0.44002135) | False | False | [] |
| unsupported-recovery: What is the measured recovery time after a North outage? | clauses/max/or | Q0: What is the measured recovery time after a North outage? (0.44002135) | False | False | [] |
| unsupported-recovery: What is the measured recovery time after a North outage? | clauses/rrf/original | Q0: What is the measured recovery time after a North outage? (0.44002135) | False | False | [] |
| unsupported-recovery: What is the measured recovery time after a North outage? | clauses/rrf/or | Q0: What is the measured recovery time after a North outage? (0.44002135) | False | False | [] |
| unsupported-recovery: What is the measured recovery time after a North outage? | topics/max/original | Q0: What is the measured recovery time after a North outage? (0.44002135) | False | False | [] |
| unsupported-recovery: What is the measured recovery time after a North outage? | topics/max/or | Q0: What is the measured recovery time after a North outage? (0.44002135) | False | False | [] |
| unsupported-recovery: What is the measured recovery time after a North outage? | topics/rrf/original | Q0: What is the measured recovery time after a North outage? (0.44002135) | False | False | [] |
| unsupported-recovery: What is the measured recovery time after a North outage? | topics/rrf/or | Q0: What is the measured recovery time after a North outage? (0.44002135) | False | False | [] |
| unsupported-recovery: What is the measured recovery time after a North outage? | combined/max/original | Q0: What is the measured recovery time after a North outage? (0.44002135) | False | False | [] |
| unsupported-recovery: What is the measured recovery time after a North outage? | combined/max/or | Q0: What is the measured recovery time after a North outage? (0.44002135) | False | False | [] |
| unsupported-recovery: What is the measured recovery time after a North outage? | combined/rrf/original | Q0: What is the measured recovery time after a North outage? (0.44002135) | False | False | [] |
| unsupported-recovery: What is the measured recovery time after a North outage? | combined/rrf/or | Q0: What is the measured recovery time after a North outage? (0.44002135) | False | False | [] |
| unrelated-rice: When should rice fields be irrigated? | original/max/original | Q0: When should rice fields be irrigated? (0.15710584) | False | False | [] |
| unrelated-rice: When should rice fields be irrigated? | clauses/max/original | Q0: When should rice fields be irrigated? (0.15710584) | False | False | [] |
| unrelated-rice: When should rice fields be irrigated? | clauses/max/or | Q0: When should rice fields be irrigated? (0.15710584) | False | False | [] |
| unrelated-rice: When should rice fields be irrigated? | clauses/rrf/original | Q0: When should rice fields be irrigated? (0.15710584) | False | False | [] |
| unrelated-rice: When should rice fields be irrigated? | clauses/rrf/or | Q0: When should rice fields be irrigated? (0.15710584) | False | False | [] |
| unrelated-rice: When should rice fields be irrigated? | topics/max/original | Q0: When should rice fields be irrigated? (0.15710584) | False | False | [] |
| unrelated-rice: When should rice fields be irrigated? | topics/max/or | Q0: When should rice fields be irrigated? (0.15710584) | False | False | [] |
| unrelated-rice: When should rice fields be irrigated? | topics/rrf/original | Q0: When should rice fields be irrigated? (0.15710584) | False | False | [] |
| unrelated-rice: When should rice fields be irrigated? | topics/rrf/or | Q0: When should rice fields be irrigated? (0.15710584) | False | False | [] |
| unrelated-rice: When should rice fields be irrigated? | combined/max/original | Q0: When should rice fields be irrigated? (0.15710584) | False | False | [] |
| unrelated-rice: When should rice fields be irrigated? | combined/max/or | Q0: When should rice fields be irrigated? (0.15710584) | False | False | [] |
| unrelated-rice: When should rice fields be irrigated? | combined/rrf/original | Q0: When should rice fields be irrigated? (0.15710584) | False | False | [] |
| unrelated-rice: When should rice fields be irrigated? | combined/rrf/or | Q0: When should rice fields be irrigated? (0.15710584) | False | False | [] |
| unrelated-bread: How much yeast should I add to sourdough bread? | original/max/original | Q0: How much yeast should I add to sourdough bread? (0.10990535) | False | False | [] |
| unrelated-bread: How much yeast should I add to sourdough bread? | clauses/max/original | Q0: How much yeast should I add to sourdough bread? (0.10990535) | False | False | [] |
| unrelated-bread: How much yeast should I add to sourdough bread? | clauses/max/or | Q0: How much yeast should I add to sourdough bread? (0.10990535) | False | False | [] |
| unrelated-bread: How much yeast should I add to sourdough bread? | clauses/rrf/original | Q0: How much yeast should I add to sourdough bread? (0.10990535) | False | False | [] |
| unrelated-bread: How much yeast should I add to sourdough bread? | clauses/rrf/or | Q0: How much yeast should I add to sourdough bread? (0.10990535) | False | False | [] |
| unrelated-bread: How much yeast should I add to sourdough bread? | topics/max/original | Q0: How much yeast should I add to sourdough bread? (0.10990535) | False | False | [] |
| unrelated-bread: How much yeast should I add to sourdough bread? | topics/max/or | Q0: How much yeast should I add to sourdough bread? (0.10990535) | False | False | [] |
| unrelated-bread: How much yeast should I add to sourdough bread? | topics/rrf/original | Q0: How much yeast should I add to sourdough bread? (0.10990535) | False | False | [] |
| unrelated-bread: How much yeast should I add to sourdough bread? | topics/rrf/or | Q0: How much yeast should I add to sourdough bread? (0.10990535) | False | False | [] |
| unrelated-bread: How much yeast should I add to sourdough bread? | combined/max/original | Q0: How much yeast should I add to sourdough bread? (0.10990535) | False | False | [] |
| unrelated-bread: How much yeast should I add to sourdough bread? | combined/max/or | Q0: How much yeast should I add to sourdough bread? (0.10990535) | False | False | [] |
| unrelated-bread: How much yeast should I add to sourdough bread? | combined/rrf/original | Q0: How much yeast should I add to sourdough bread? (0.10990535) | False | False | [] |
| unrelated-bread: How much yeast should I add to sourdough bread? | combined/rrf/or | Q0: How much yeast should I add to sourdough bread? (0.10990535) | False | False | [] |
| unrelated-moon: How many moons orbit Neptune? | original/max/original | Q0: How many moons orbit Neptune? (0.08359429) | False | False | [] |
| unrelated-moon: How many moons orbit Neptune? | clauses/max/original | Q0: How many moons orbit Neptune? (0.08359429) | False | False | [] |
| unrelated-moon: How many moons orbit Neptune? | clauses/max/or | Q0: How many moons orbit Neptune? (0.08359429) | False | False | [] |
| unrelated-moon: How many moons orbit Neptune? | clauses/rrf/original | Q0: How many moons orbit Neptune? (0.08359429) | False | False | [] |
| unrelated-moon: How many moons orbit Neptune? | clauses/rrf/or | Q0: How many moons orbit Neptune? (0.08359429) | False | False | [] |
| unrelated-moon: How many moons orbit Neptune? | topics/max/original | Q0: How many moons orbit Neptune? (0.08359429) | False | False | [] |
| unrelated-moon: How many moons orbit Neptune? | topics/max/or | Q0: How many moons orbit Neptune? (0.08359429) | False | False | [] |
| unrelated-moon: How many moons orbit Neptune? | topics/rrf/original | Q0: How many moons orbit Neptune? (0.08359429) | False | False | [] |
| unrelated-moon: How many moons orbit Neptune? | topics/rrf/or | Q0: How many moons orbit Neptune? (0.08359429) | False | False | [] |
| unrelated-moon: How many moons orbit Neptune? | combined/max/original | Q0: How many moons orbit Neptune? (0.08359429) | False | False | [] |
| unrelated-moon: How many moons orbit Neptune? | combined/max/or | Q0: How many moons orbit Neptune? (0.08359429) | False | False | [] |
| unrelated-moon: How many moons orbit Neptune? | combined/rrf/original | Q0: How many moons orbit Neptune? (0.08359429) | False | False | [] |
| unrelated-moon: How many moons orbit Neptune? | combined/rrf/or | Q0: How many moons orbit Neptune? (0.08359429) | False | False | [] |
| unrelated-piano: How should I tune a grand piano? | original/max/original | Q0: How should I tune a grand piano? (0.05855029) | False | False | [] |
| unrelated-piano: How should I tune a grand piano? | clauses/max/original | Q0: How should I tune a grand piano? (0.05855029) | False | False | [] |
| unrelated-piano: How should I tune a grand piano? | clauses/max/or | Q0: How should I tune a grand piano? (0.05855029) | False | False | [] |
| unrelated-piano: How should I tune a grand piano? | clauses/rrf/original | Q0: How should I tune a grand piano? (0.05855029) | False | False | [] |
| unrelated-piano: How should I tune a grand piano? | clauses/rrf/or | Q0: How should I tune a grand piano? (0.05855029) | False | False | [] |
| unrelated-piano: How should I tune a grand piano? | topics/max/original | Q0: How should I tune a grand piano? (0.05855029) | False | False | [] |
| unrelated-piano: How should I tune a grand piano? | topics/max/or | Q0: How should I tune a grand piano? (0.05855029) | False | False | [] |
| unrelated-piano: How should I tune a grand piano? | topics/rrf/original | Q0: How should I tune a grand piano? (0.05855029) | False | False | [] |
| unrelated-piano: How should I tune a grand piano? | topics/rrf/or | Q0: How should I tune a grand piano? (0.05855029) | False | False | [] |
| unrelated-piano: How should I tune a grand piano? | combined/max/original | Q0: How should I tune a grand piano? (0.05855029) | False | False | [] |
| unrelated-piano: How should I tune a grand piano? | combined/max/or | Q0: How should I tune a grand piano? (0.05855029) | False | False | [] |
| unrelated-piano: How should I tune a grand piano? | combined/rrf/original | Q0: How should I tune a grand piano? (0.05855029) | False | False | [] |
| unrelated-piano: How should I tune a grand piano? | combined/rrf/or | Q0: How should I tune a grand piano? (0.05855029) | False | False | [] |
| unrelated-bird: Why do swallows migrate south? | original/max/original | Q0: Why do swallows migrate south? (0.15400278) | False | False | [] |
| unrelated-bird: Why do swallows migrate south? | clauses/max/original | Q0: Why do swallows migrate south? (0.15400278) | False | False | [] |
| unrelated-bird: Why do swallows migrate south? | clauses/max/or | Q0: Why do swallows migrate south? (0.15400278) | False | False | [] |
| unrelated-bird: Why do swallows migrate south? | clauses/rrf/original | Q0: Why do swallows migrate south? (0.15400278) | False | False | [] |
| unrelated-bird: Why do swallows migrate south? | clauses/rrf/or | Q0: Why do swallows migrate south? (0.15400278) | False | False | [] |
| unrelated-bird: Why do swallows migrate south? | topics/max/original | Q0: Why do swallows migrate south? (0.15400278) | False | False | [] |
| unrelated-bird: Why do swallows migrate south? | topics/max/or | Q0: Why do swallows migrate south? (0.15400278) | False | False | [] |
| unrelated-bird: Why do swallows migrate south? | topics/rrf/original | Q0: Why do swallows migrate south? (0.15400278) | False | False | [] |
| unrelated-bird: Why do swallows migrate south? | topics/rrf/or | Q0: Why do swallows migrate south? (0.15400278) | False | False | [] |
| unrelated-bird: Why do swallows migrate south? | combined/max/original | Q0: Why do swallows migrate south? (0.15400278) | False | False | [] |
| unrelated-bird: Why do swallows migrate south? | combined/max/or | Q0: Why do swallows migrate south? (0.15400278) | False | False | [] |
| unrelated-bird: Why do swallows migrate south? | combined/rrf/original | Q0: Why do swallows migrate south? (0.15400278) | False | False | [] |
| unrelated-bird: Why do swallows migrate south? | combined/rrf/or | Q0: Why do swallows migrate south? (0.15400278) | False | False | [] |
| ambiguous-expiration: When does it expire? | original/max/original | Q0: When does it expire? (0.45293337) | False | False | [] |
| ambiguous-expiration: When does it expire? | clauses/max/original | Q0: When does it expire? (0.45293337) | False | False | [] |
| ambiguous-expiration: When does it expire? | clauses/max/or | Q0: When does it expire? (0.45293337) | False | False | [] |
| ambiguous-expiration: When does it expire? | clauses/rrf/original | Q0: When does it expire? (0.45293337) | False | False | [] |
| ambiguous-expiration: When does it expire? | clauses/rrf/or | Q0: When does it expire? (0.45293337) | False | False | [] |
| ambiguous-expiration: When does it expire? | topics/max/original | Q0: When does it expire? (0.45293337) | False | False | [] |
| ambiguous-expiration: When does it expire? | topics/max/or | Q0: When does it expire? (0.45293337) | False | False | [] |
| ambiguous-expiration: When does it expire? | topics/rrf/original | Q0: When does it expire? (0.45293337) | False | False | [] |
| ambiguous-expiration: When does it expire? | topics/rrf/or | Q0: When does it expire? (0.45293337) | False | False | [] |
| ambiguous-expiration: When does it expire? | combined/max/original | Q0: When does it expire? (0.45293337) | False | False | [] |
| ambiguous-expiration: When does it expire? | combined/max/or | Q0: When does it expire? (0.45293337) | False | False | [] |
| ambiguous-expiration: When does it expire? | combined/rrf/original | Q0: When does it expire? (0.45293337) | False | False | [] |
| ambiguous-expiration: When does it expire? | combined/rrf/or | Q0: When does it expire? (0.45293337) | False | False | [] |
| ambiguous-best: Which service is the best? | original/max/original | Q0: Which service is the best? (0.32897553) | False | False | [] |
| ambiguous-best: Which service is the best? | clauses/max/original | Q0: Which service is the best? (0.32897553) | False | False | [] |
| ambiguous-best: Which service is the best? | clauses/max/or | Q0: Which service is the best? (0.32897553) | False | False | [] |
| ambiguous-best: Which service is the best? | clauses/rrf/original | Q0: Which service is the best? (0.32897553) | False | False | [] |
| ambiguous-best: Which service is the best? | clauses/rrf/or | Q0: Which service is the best? (0.32897553) | False | False | [] |
| ambiguous-best: Which service is the best? | topics/max/original | Q0: Which service is the best? (0.32897553) | False | False | [] |
| ambiguous-best: Which service is the best? | topics/max/or | Q0: Which service is the best? (0.32897553) | False | False | [] |
| ambiguous-best: Which service is the best? | topics/rrf/original | Q0: Which service is the best? (0.32897553) | False | False | [] |
| ambiguous-best: Which service is the best? | topics/rrf/or | Q0: Which service is the best? (0.32897553) | False | False | [] |
| ambiguous-best: Which service is the best? | combined/max/original | Q0: Which service is the best? (0.32897553) | False | False | [] |
| ambiguous-best: Which service is the best? | combined/max/or | Q0: Which service is the best? (0.32897553) | False | False | [] |
| ambiguous-best: Which service is the best? | combined/rrf/original | Q0: Which service is the best? (0.32897553) | False | False | [] |
| ambiguous-best: Which service is the best? | combined/rrf/or | Q0: Which service is the best? (0.32897553) | False | False | [] |
| ambiguous-safety: Is everything secure? | original/max/original | Q0: Is everything secure? (0.20144689) | False | False | [] |
| ambiguous-safety: Is everything secure? | clauses/max/original | Q0: Is everything secure? (0.20144689) | False | False | [] |
| ambiguous-safety: Is everything secure? | clauses/max/or | Q0: Is everything secure? (0.20144689) | False | False | [] |
| ambiguous-safety: Is everything secure? | clauses/rrf/original | Q0: Is everything secure? (0.20144689) | False | False | [] |
| ambiguous-safety: Is everything secure? | clauses/rrf/or | Q0: Is everything secure? (0.20144689) | False | False | [] |
| ambiguous-safety: Is everything secure? | topics/max/original | Q0: Is everything secure? (0.20144689) | False | False | [] |
| ambiguous-safety: Is everything secure? | topics/max/or | Q0: Is everything secure? (0.20144689) | False | False | [] |
| ambiguous-safety: Is everything secure? | topics/rrf/original | Q0: Is everything secure? (0.20144689) | False | False | [] |
| ambiguous-safety: Is everything secure? | topics/rrf/or | Q0: Is everything secure? (0.20144689) | False | False | [] |
| ambiguous-safety: Is everything secure? | combined/max/original | Q0: Is everything secure? (0.20144689) | False | False | [] |
| ambiguous-safety: Is everything secure? | combined/max/or | Q0: Is everything secure? (0.20144689) | False | False | [] |
| ambiguous-safety: Is everything secure? | combined/rrf/original | Q0: Is everything secure? (0.20144689) | False | False | [] |
| ambiguous-safety: Is everything secure? | combined/rrf/or | Q0: Is everything secure? (0.20144689) | False | False | [] |

## Safety summary (4 answerable / 6 unsupported)

| Candidate | TP/TN/FP/FN | P/R/F1 | Complete /4 | Calls / multiplier / max | Added non-required |
|---|---|---|---|---|---|
| original/max/original | 3/5/1/1 | 0.75000/0.75000/0.75000 | 2 | 10 / 1.00x / 1 | 0 |
| clauses/max/original | 3/5/1/1 | 0.75000/0.75000/0.75000 | 2 | 16 / 1.60x / 3 | 2 |
| clauses/max/or | 3/5/1/1 | 0.75000/0.75000/0.75000 | 2 | 16 / 1.60x / 3 | 2 |
| clauses/rrf/original | 3/5/1/1 | 0.75000/0.75000/0.75000 | 2 | 16 / 1.60x / 3 | 1 |
| clauses/rrf/or | 3/5/1/1 | 0.75000/0.75000/0.75000 | 2 | 16 / 1.60x / 3 | 1 |
| topics/max/original | 3/5/1/1 | 0.75000/0.75000/0.75000 | 2 | 19 / 1.90x / 4 | 1 |
| topics/max/or | 3/3/3/1 | 0.50000/0.75000/0.60000 | 2 | 19 / 1.90x / 4 | 9 |
| topics/rrf/original | 3/5/1/1 | 0.75000/0.75000/0.75000 | 2 | 19 / 1.90x / 4 | 0 |
| topics/rrf/or | 3/3/3/1 | 0.50000/0.75000/0.60000 | 2 | 19 / 1.90x / 4 | 7 |
| combined/max/original | 3/5/1/1 | 0.75000/0.75000/0.75000 | 2 | 25 / 2.50x / 4 | 3 |
| combined/max/or | 3/3/3/1 | 0.50000/0.75000/0.60000 | 2 | 25 / 2.50x / 4 | 11 |
| combined/rrf/original | 3/5/1/1 | 0.75000/0.75000/0.75000 | 2 | 25 / 2.50x / 4 | 1 |
| combined/rrf/or | 3/3/3/1 | 0.50000/0.75000/0.60000 | 2 | 25 / 2.50x / 4 | 8 |

## Phase 15-only safety set (separate denominator)

| Case | Candidate | Queries / top cosine | Gate | New FP | Complete | Added non-required |
|---|---|---|---|---|---|---|
| safety-related-signature | original/max/original | How does JWT authentication work and why is its signature checked? (0.78078) | True | False | True | [] |
| safety-three-topics | original/max/original | Explain JWT authentication, MongoDB storage, and cloud service models. (0.54308) | True | False | False | [] |
| safety-unsupported-rotation | original/max/original | Explain JWT signing-key rotation and emergency key revocation. (0.49871) | False | False | False | [] |
| safety-unrelated-conjunction | original/max/original | How do irrigation and fertilizer improve rice farming? (0.15741) | False | False | False | [] |
| safety-single-concept | original/max/original | Research and development (0.23648) | False | False | False | [] |
| safety-explicit-questions | original/max/original | Where are original PDFs stored? How long do access tokens remain valid? (0.52362) | True | False | True | [] |
| safety-semicolon-unsupported | original/max/original | How long are refresh tokens stored; what is the JWT signing-key rotation policy? (0.52264) | True | False | False | [] |
| safety-mixed-supported-unsupported | original/max/original | Explain JWT signatures and rice irrigation. (0.45187) | False | False | False | [] |
| safety-comparison-unsupported | original/max/original | Compare what the customer manages in IaaS and the lunar habitat. (0.26398) | False | False | False | [] |
| safety-semicolon-supported | original/max/original | Where is application metadata stored; which algorithm stores password hashes? (0.45132) | False | False | False | [] |
| safety-related-signature | clauses/max/original | How does JWT authentication work and why is its signature checked? (0.78078) | True | False | True | [] |
| safety-three-topics | clauses/max/original | Explain JWT authentication, MongoDB storage, and cloud service models. (0.54308) | True | False | False | [] |
| safety-unsupported-rotation | clauses/max/original | Explain JWT signing-key rotation and emergency key revocation. (0.49871) | False | False | False | [] |
| safety-unrelated-conjunction | clauses/max/original | How do irrigation and fertilizer improve rice farming? (0.15741) | False | False | False | [] |
| safety-single-concept | clauses/max/original | Research and development (0.23648) | False | False | False | [] |
| safety-explicit-questions | clauses/max/original | Where are original PDFs stored? How long do access tokens remain valid? (0.52362); Where are original PDFs stored (0.65561); How long do access tokens remain valid (0.68320) | True | False | True | [{'document': 'operations-guide.pdf', 'page_start': 2, 'page_end': 2}, {'document': 'operations-guide.pdf', 'page_start': 3, 'page_end': 3}] |
| safety-semicolon-unsupported | clauses/max/original | How long are refresh tokens stored; what is the JWT signing-key rotation policy? (0.52264); How long are refresh tokens stored (0.57489); what is the JWT signing-key rotation policy (0.56694) | True | False | False | [] |
| safety-mixed-supported-unsupported | clauses/max/original | Explain JWT signatures and rice irrigation. (0.45187) | False | False | False | [] |
| safety-comparison-unsupported | clauses/max/original | Compare what the customer manages in IaaS and the lunar habitat. (0.26398) | False | False | False | [] |
| safety-semicolon-supported | clauses/max/original | Where is application metadata stored; which algorithm stores password hashes? (0.45132); Where is application metadata stored (0.46045); which algorithm stores password hashes (0.45245) | False | False | False | [] |
| safety-related-signature | clauses/max/or | How does JWT authentication work and why is its signature checked? (0.78078) | True | False | True | [] |
| safety-three-topics | clauses/max/or | Explain JWT authentication, MongoDB storage, and cloud service models. (0.54308) | True | False | False | [] |
| safety-unsupported-rotation | clauses/max/or | Explain JWT signing-key rotation and emergency key revocation. (0.49871) | False | False | False | [] |
| safety-unrelated-conjunction | clauses/max/or | How do irrigation and fertilizer improve rice farming? (0.15741) | False | False | False | [] |
| safety-single-concept | clauses/max/or | Research and development (0.23648) | False | False | False | [] |
| safety-explicit-questions | clauses/max/or | Where are original PDFs stored? How long do access tokens remain valid? (0.52362); Where are original PDFs stored (0.65561); How long do access tokens remain valid (0.68320) | True | False | True | [{'document': 'operations-guide.pdf', 'page_start': 2, 'page_end': 2}, {'document': 'operations-guide.pdf', 'page_start': 3, 'page_end': 3}] |
| safety-semicolon-unsupported | clauses/max/or | How long are refresh tokens stored; what is the JWT signing-key rotation policy? (0.52264); How long are refresh tokens stored (0.57489); what is the JWT signing-key rotation policy (0.56694) | True | False | False | [] |
| safety-mixed-supported-unsupported | clauses/max/or | Explain JWT signatures and rice irrigation. (0.45187) | False | False | False | [] |
| safety-comparison-unsupported | clauses/max/or | Compare what the customer manages in IaaS and the lunar habitat. (0.26398) | False | False | False | [] |
| safety-semicolon-supported | clauses/max/or | Where is application metadata stored; which algorithm stores password hashes? (0.45132); Where is application metadata stored (0.46045); which algorithm stores password hashes (0.45245) | False | False | False | [] |
| safety-related-signature | clauses/rrf/original | How does JWT authentication work and why is its signature checked? (0.78078) | True | False | True | [] |
| safety-three-topics | clauses/rrf/original | Explain JWT authentication, MongoDB storage, and cloud service models. (0.54308) | True | False | False | [] |
| safety-unsupported-rotation | clauses/rrf/original | Explain JWT signing-key rotation and emergency key revocation. (0.49871) | False | False | False | [] |
| safety-unrelated-conjunction | clauses/rrf/original | How do irrigation and fertilizer improve rice farming? (0.15741) | False | False | False | [] |
| safety-single-concept | clauses/rrf/original | Research and development (0.23648) | False | False | False | [] |
| safety-explicit-questions | clauses/rrf/original | Where are original PDFs stored? How long do access tokens remain valid? (0.52362); Where are original PDFs stored (0.65561); How long do access tokens remain valid (0.68320) | True | False | True | [{'document': 'storage-guide.pdf', 'page_start': 3, 'page_end': 3}] |
| safety-semicolon-unsupported | clauses/rrf/original | How long are refresh tokens stored; what is the JWT signing-key rotation policy? (0.52264); How long are refresh tokens stored (0.57489); what is the JWT signing-key rotation policy (0.56694) | True | False | False | [] |
| safety-mixed-supported-unsupported | clauses/rrf/original | Explain JWT signatures and rice irrigation. (0.45187) | False | False | False | [] |
| safety-comparison-unsupported | clauses/rrf/original | Compare what the customer manages in IaaS and the lunar habitat. (0.26398) | False | False | False | [] |
| safety-semicolon-supported | clauses/rrf/original | Where is application metadata stored; which algorithm stores password hashes? (0.45132); Where is application metadata stored (0.46045); which algorithm stores password hashes (0.45245) | False | False | False | [] |
| safety-related-signature | clauses/rrf/or | How does JWT authentication work and why is its signature checked? (0.78078) | True | False | True | [] |
| safety-three-topics | clauses/rrf/or | Explain JWT authentication, MongoDB storage, and cloud service models. (0.54308) | True | False | False | [] |
| safety-unsupported-rotation | clauses/rrf/or | Explain JWT signing-key rotation and emergency key revocation. (0.49871) | False | False | False | [] |
| safety-unrelated-conjunction | clauses/rrf/or | How do irrigation and fertilizer improve rice farming? (0.15741) | False | False | False | [] |
| safety-single-concept | clauses/rrf/or | Research and development (0.23648) | False | False | False | [] |
| safety-explicit-questions | clauses/rrf/or | Where are original PDFs stored? How long do access tokens remain valid? (0.52362); Where are original PDFs stored (0.65561); How long do access tokens remain valid (0.68320) | True | False | True | [{'document': 'storage-guide.pdf', 'page_start': 3, 'page_end': 3}] |
| safety-semicolon-unsupported | clauses/rrf/or | How long are refresh tokens stored; what is the JWT signing-key rotation policy? (0.52264); How long are refresh tokens stored (0.57489); what is the JWT signing-key rotation policy (0.56694) | True | False | False | [] |
| safety-mixed-supported-unsupported | clauses/rrf/or | Explain JWT signatures and rice irrigation. (0.45187) | False | False | False | [] |
| safety-comparison-unsupported | clauses/rrf/or | Compare what the customer manages in IaaS and the lunar habitat. (0.26398) | False | False | False | [] |
| safety-semicolon-supported | clauses/rrf/or | Where is application metadata stored; which algorithm stores password hashes? (0.45132); Where is application metadata stored (0.46045); which algorithm stores password hashes (0.45245) | False | False | False | [] |
| safety-related-signature | topics/max/original | How does JWT authentication work and why is its signature checked? (0.78078) | True | False | True | [] |
| safety-three-topics | topics/max/original | Explain JWT authentication, MongoDB storage, and cloud service models. (0.54308); JWT authentication (0.69912); MongoDB storage (0.64942); cloud service models (0.42488) | True | False | False | [{'document': 'storage-guide.pdf', 'page_start': 1, 'page_end': 1}] |
| safety-unsupported-rotation | topics/max/original | Explain JWT signing-key rotation and emergency key revocation. (0.49871); JWT signing-key rotation (0.50353); emergency key revocation (0.31762) | False | False | False | [] |
| safety-unrelated-conjunction | topics/max/original | How do irrigation and fertilizer improve rice farming? (0.15741) | False | False | False | [] |
| safety-single-concept | topics/max/original | Research and development (0.23648) | False | False | False | [] |
| safety-explicit-questions | topics/max/original | Where are original PDFs stored? How long do access tokens remain valid? (0.52362) | True | False | True | [] |
| safety-semicolon-unsupported | topics/max/original | How long are refresh tokens stored; what is the JWT signing-key rotation policy? (0.52264) | True | False | False | [] |
| safety-mixed-supported-unsupported | topics/max/original | Explain JWT signatures and rice irrigation. (0.45187); JWT signatures (0.71713); rice irrigation (0.12477) | False | False | False | [] |
| safety-comparison-unsupported | topics/max/original | Compare what the customer manages in IaaS and the lunar habitat. (0.26398); Compare what the customer manages in IaaS (0.28087); Compare what the customer manages in the lunar habitat (0.24453) | False | False | False | [] |
| safety-semicolon-supported | topics/max/original | Where is application metadata stored; which algorithm stores password hashes? (0.45132) | False | False | False | [] |
| safety-related-signature | topics/max/or | How does JWT authentication work and why is its signature checked? (0.78078) | True | False | True | [] |
| safety-three-topics | topics/max/or | Explain JWT authentication, MongoDB storage, and cloud service models. (0.54308); JWT authentication (0.69912); MongoDB storage (0.64942); cloud service models (0.42488) | True | False | False | [{'document': 'storage-guide.pdf', 'page_start': 1, 'page_end': 1}] |
| safety-unsupported-rotation | topics/max/or | Explain JWT signing-key rotation and emergency key revocation. (0.49871); JWT signing-key rotation (0.50353); emergency key revocation (0.31762) | True | True | False | [{'document': 'authentication-guide.pdf', 'page_start': 1, 'page_end': 1}, {'document': 'operations-guide.pdf', 'page_start': 3, 'page_end': 3}, {'document': 'web-guide.pdf', 'page_start': 1, 'page_end': 1}, {'document': 'web-guide.pdf', 'page_start': 2, 'page_end': 2}] |
| safety-unrelated-conjunction | topics/max/or | How do irrigation and fertilizer improve rice farming? (0.15741) | False | False | False | [] |
| safety-single-concept | topics/max/or | Research and development (0.23648) | False | False | False | [] |
| safety-explicit-questions | topics/max/or | Where are original PDFs stored? How long do access tokens remain valid? (0.52362) | True | False | True | [] |
| safety-semicolon-unsupported | topics/max/or | How long are refresh tokens stored; what is the JWT signing-key rotation policy? (0.52264) | True | False | False | [] |
| safety-mixed-supported-unsupported | topics/max/or | Explain JWT signatures and rice irrigation. (0.45187); JWT signatures (0.71713); rice irrigation (0.12477) | True | True | False | [{'document': 'authentication-guide.pdf', 'page_start': 1, 'page_end': 1}, {'document': 'web-guide.pdf', 'page_start': 1, 'page_end': 1}, {'document': 'operations-guide.pdf', 'page_start': 3, 'page_end': 3}, {'document': 'operations-guide.pdf', 'page_start': 2, 'page_end': 2}] |
| safety-comparison-unsupported | topics/max/or | Compare what the customer manages in IaaS and the lunar habitat. (0.26398); Compare what the customer manages in IaaS (0.28087); Compare what the customer manages in the lunar habitat (0.24453) | False | False | False | [] |
| safety-semicolon-supported | topics/max/or | Where is application metadata stored; which algorithm stores password hashes? (0.45132) | False | False | False | [] |
| safety-related-signature | topics/rrf/original | How does JWT authentication work and why is its signature checked? (0.78078) | True | False | True | [] |
| safety-three-topics | topics/rrf/original | Explain JWT authentication, MongoDB storage, and cloud service models. (0.54308); JWT authentication (0.69912); MongoDB storage (0.64942); cloud service models (0.42488) | True | False | False | [] |
| safety-unsupported-rotation | topics/rrf/original | Explain JWT signing-key rotation and emergency key revocation. (0.49871); JWT signing-key rotation (0.50353); emergency key revocation (0.31762) | False | False | False | [] |
| safety-unrelated-conjunction | topics/rrf/original | How do irrigation and fertilizer improve rice farming? (0.15741) | False | False | False | [] |
| safety-single-concept | topics/rrf/original | Research and development (0.23648) | False | False | False | [] |
| safety-explicit-questions | topics/rrf/original | Where are original PDFs stored? How long do access tokens remain valid? (0.52362) | True | False | True | [] |
| safety-semicolon-unsupported | topics/rrf/original | How long are refresh tokens stored; what is the JWT signing-key rotation policy? (0.52264) | True | False | False | [] |
| safety-mixed-supported-unsupported | topics/rrf/original | Explain JWT signatures and rice irrigation. (0.45187); JWT signatures (0.71713); rice irrigation (0.12477) | False | False | False | [] |
| safety-comparison-unsupported | topics/rrf/original | Compare what the customer manages in IaaS and the lunar habitat. (0.26398); Compare what the customer manages in IaaS (0.28087); Compare what the customer manages in the lunar habitat (0.24453) | False | False | False | [] |
| safety-semicolon-supported | topics/rrf/original | Where is application metadata stored; which algorithm stores password hashes? (0.45132) | False | False | False | [] |
| safety-related-signature | topics/rrf/or | How does JWT authentication work and why is its signature checked? (0.78078) | True | False | True | [] |
| safety-three-topics | topics/rrf/or | Explain JWT authentication, MongoDB storage, and cloud service models. (0.54308); JWT authentication (0.69912); MongoDB storage (0.64942); cloud service models (0.42488) | True | False | False | [] |
| safety-unsupported-rotation | topics/rrf/or | Explain JWT signing-key rotation and emergency key revocation. (0.49871); JWT signing-key rotation (0.50353); emergency key revocation (0.31762) | True | True | False | [{'document': 'operations-guide.pdf', 'page_start': 3, 'page_end': 3}, {'document': 'authentication-guide.pdf', 'page_start': 1, 'page_end': 1}, {'document': 'web-guide.pdf', 'page_start': 2, 'page_end': 2}, {'document': 'web-guide.pdf', 'page_start': 1, 'page_end': 1}] |
| safety-unrelated-conjunction | topics/rrf/or | How do irrigation and fertilizer improve rice farming? (0.15741) | False | False | False | [] |
| safety-single-concept | topics/rrf/or | Research and development (0.23648) | False | False | False | [] |
| safety-explicit-questions | topics/rrf/or | Where are original PDFs stored? How long do access tokens remain valid? (0.52362) | True | False | True | [] |
| safety-semicolon-unsupported | topics/rrf/or | How long are refresh tokens stored; what is the JWT signing-key rotation policy? (0.52264) | True | False | False | [] |
| safety-mixed-supported-unsupported | topics/rrf/or | Explain JWT signatures and rice irrigation. (0.45187); JWT signatures (0.71713); rice irrigation (0.12477) | True | True | False | [{'document': 'authentication-guide.pdf', 'page_start': 1, 'page_end': 1}, {'document': 'web-guide.pdf', 'page_start': 1, 'page_end': 1}, {'document': 'operations-guide.pdf', 'page_start': 3, 'page_end': 3}] |
| safety-comparison-unsupported | topics/rrf/or | Compare what the customer manages in IaaS and the lunar habitat. (0.26398); Compare what the customer manages in IaaS (0.28087); Compare what the customer manages in the lunar habitat (0.24453) | False | False | False | [] |
| safety-semicolon-supported | topics/rrf/or | Where is application metadata stored; which algorithm stores password hashes? (0.45132) | False | False | False | [] |
| safety-related-signature | combined/max/original | How does JWT authentication work and why is its signature checked? (0.78078) | True | False | True | [] |
| safety-three-topics | combined/max/original | Explain JWT authentication, MongoDB storage, and cloud service models. (0.54308); JWT authentication (0.69912); MongoDB storage (0.64942); cloud service models (0.42488) | True | False | False | [{'document': 'storage-guide.pdf', 'page_start': 1, 'page_end': 1}] |
| safety-unsupported-rotation | combined/max/original | Explain JWT signing-key rotation and emergency key revocation. (0.49871); JWT signing-key rotation (0.50353); emergency key revocation (0.31762) | False | False | False | [] |
| safety-unrelated-conjunction | combined/max/original | How do irrigation and fertilizer improve rice farming? (0.15741) | False | False | False | [] |
| safety-single-concept | combined/max/original | Research and development (0.23648) | False | False | False | [] |
| safety-explicit-questions | combined/max/original | Where are original PDFs stored? How long do access tokens remain valid? (0.52362); Where are original PDFs stored (0.65561); How long do access tokens remain valid (0.68320) | True | False | True | [{'document': 'operations-guide.pdf', 'page_start': 2, 'page_end': 2}, {'document': 'operations-guide.pdf', 'page_start': 3, 'page_end': 3}] |
| safety-semicolon-unsupported | combined/max/original | How long are refresh tokens stored; what is the JWT signing-key rotation policy? (0.52264); How long are refresh tokens stored (0.57489); what is the JWT signing-key rotation policy (0.56694) | True | False | False | [] |
| safety-mixed-supported-unsupported | combined/max/original | Explain JWT signatures and rice irrigation. (0.45187); JWT signatures (0.71713); rice irrigation (0.12477) | False | False | False | [] |
| safety-comparison-unsupported | combined/max/original | Compare what the customer manages in IaaS and the lunar habitat. (0.26398); Compare what the customer manages in IaaS (0.28087); Compare what the customer manages in the lunar habitat (0.24453) | False | False | False | [] |
| safety-semicolon-supported | combined/max/original | Where is application metadata stored; which algorithm stores password hashes? (0.45132); Where is application metadata stored (0.46045); which algorithm stores password hashes (0.45245) | False | False | False | [] |
| safety-related-signature | combined/max/or | How does JWT authentication work and why is its signature checked? (0.78078) | True | False | True | [] |
| safety-three-topics | combined/max/or | Explain JWT authentication, MongoDB storage, and cloud service models. (0.54308); JWT authentication (0.69912); MongoDB storage (0.64942); cloud service models (0.42488) | True | False | False | [{'document': 'storage-guide.pdf', 'page_start': 1, 'page_end': 1}] |
| safety-unsupported-rotation | combined/max/or | Explain JWT signing-key rotation and emergency key revocation. (0.49871); JWT signing-key rotation (0.50353); emergency key revocation (0.31762) | True | True | False | [{'document': 'authentication-guide.pdf', 'page_start': 1, 'page_end': 1}, {'document': 'operations-guide.pdf', 'page_start': 3, 'page_end': 3}, {'document': 'web-guide.pdf', 'page_start': 1, 'page_end': 1}, {'document': 'web-guide.pdf', 'page_start': 2, 'page_end': 2}] |
| safety-unrelated-conjunction | combined/max/or | How do irrigation and fertilizer improve rice farming? (0.15741) | False | False | False | [] |
| safety-single-concept | combined/max/or | Research and development (0.23648) | False | False | False | [] |
| safety-explicit-questions | combined/max/or | Where are original PDFs stored? How long do access tokens remain valid? (0.52362); Where are original PDFs stored (0.65561); How long do access tokens remain valid (0.68320) | True | False | True | [{'document': 'operations-guide.pdf', 'page_start': 2, 'page_end': 2}, {'document': 'operations-guide.pdf', 'page_start': 3, 'page_end': 3}] |
| safety-semicolon-unsupported | combined/max/or | How long are refresh tokens stored; what is the JWT signing-key rotation policy? (0.52264); How long are refresh tokens stored (0.57489); what is the JWT signing-key rotation policy (0.56694) | True | False | False | [] |
| safety-mixed-supported-unsupported | combined/max/or | Explain JWT signatures and rice irrigation. (0.45187); JWT signatures (0.71713); rice irrigation (0.12477) | True | True | False | [{'document': 'authentication-guide.pdf', 'page_start': 1, 'page_end': 1}, {'document': 'web-guide.pdf', 'page_start': 1, 'page_end': 1}, {'document': 'operations-guide.pdf', 'page_start': 3, 'page_end': 3}, {'document': 'operations-guide.pdf', 'page_start': 2, 'page_end': 2}] |
| safety-comparison-unsupported | combined/max/or | Compare what the customer manages in IaaS and the lunar habitat. (0.26398); Compare what the customer manages in IaaS (0.28087); Compare what the customer manages in the lunar habitat (0.24453) | False | False | False | [] |
| safety-semicolon-supported | combined/max/or | Where is application metadata stored; which algorithm stores password hashes? (0.45132); Where is application metadata stored (0.46045); which algorithm stores password hashes (0.45245) | False | False | False | [] |
| safety-related-signature | combined/rrf/original | How does JWT authentication work and why is its signature checked? (0.78078) | True | False | True | [] |
| safety-three-topics | combined/rrf/original | Explain JWT authentication, MongoDB storage, and cloud service models. (0.54308); JWT authentication (0.69912); MongoDB storage (0.64942); cloud service models (0.42488) | True | False | False | [] |
| safety-unsupported-rotation | combined/rrf/original | Explain JWT signing-key rotation and emergency key revocation. (0.49871); JWT signing-key rotation (0.50353); emergency key revocation (0.31762) | False | False | False | [] |
| safety-unrelated-conjunction | combined/rrf/original | How do irrigation and fertilizer improve rice farming? (0.15741) | False | False | False | [] |
| safety-single-concept | combined/rrf/original | Research and development (0.23648) | False | False | False | [] |
| safety-explicit-questions | combined/rrf/original | Where are original PDFs stored? How long do access tokens remain valid? (0.52362); Where are original PDFs stored (0.65561); How long do access tokens remain valid (0.68320) | True | False | True | [{'document': 'storage-guide.pdf', 'page_start': 3, 'page_end': 3}] |
| safety-semicolon-unsupported | combined/rrf/original | How long are refresh tokens stored; what is the JWT signing-key rotation policy? (0.52264); How long are refresh tokens stored (0.57489); what is the JWT signing-key rotation policy (0.56694) | True | False | False | [] |
| safety-mixed-supported-unsupported | combined/rrf/original | Explain JWT signatures and rice irrigation. (0.45187); JWT signatures (0.71713); rice irrigation (0.12477) | False | False | False | [] |
| safety-comparison-unsupported | combined/rrf/original | Compare what the customer manages in IaaS and the lunar habitat. (0.26398); Compare what the customer manages in IaaS (0.28087); Compare what the customer manages in the lunar habitat (0.24453) | False | False | False | [] |
| safety-semicolon-supported | combined/rrf/original | Where is application metadata stored; which algorithm stores password hashes? (0.45132); Where is application metadata stored (0.46045); which algorithm stores password hashes (0.45245) | False | False | False | [] |
| safety-related-signature | combined/rrf/or | How does JWT authentication work and why is its signature checked? (0.78078) | True | False | True | [] |
| safety-three-topics | combined/rrf/or | Explain JWT authentication, MongoDB storage, and cloud service models. (0.54308); JWT authentication (0.69912); MongoDB storage (0.64942); cloud service models (0.42488) | True | False | False | [] |
| safety-unsupported-rotation | combined/rrf/or | Explain JWT signing-key rotation and emergency key revocation. (0.49871); JWT signing-key rotation (0.50353); emergency key revocation (0.31762) | True | True | False | [{'document': 'operations-guide.pdf', 'page_start': 3, 'page_end': 3}, {'document': 'authentication-guide.pdf', 'page_start': 1, 'page_end': 1}, {'document': 'web-guide.pdf', 'page_start': 2, 'page_end': 2}, {'document': 'web-guide.pdf', 'page_start': 1, 'page_end': 1}] |
| safety-unrelated-conjunction | combined/rrf/or | How do irrigation and fertilizer improve rice farming? (0.15741) | False | False | False | [] |
| safety-single-concept | combined/rrf/or | Research and development (0.23648) | False | False | False | [] |
| safety-explicit-questions | combined/rrf/or | Where are original PDFs stored? How long do access tokens remain valid? (0.52362); Where are original PDFs stored (0.65561); How long do access tokens remain valid (0.68320) | True | False | True | [{'document': 'storage-guide.pdf', 'page_start': 3, 'page_end': 3}] |
| safety-semicolon-unsupported | combined/rrf/or | How long are refresh tokens stored; what is the JWT signing-key rotation policy? (0.52264); How long are refresh tokens stored (0.57489); what is the JWT signing-key rotation policy (0.56694) | True | False | False | [] |
| safety-mixed-supported-unsupported | combined/rrf/or | Explain JWT signatures and rice irrigation. (0.45187); JWT signatures (0.71713); rice irrigation (0.12477) | True | True | False | [{'document': 'authentication-guide.pdf', 'page_start': 1, 'page_end': 1}, {'document': 'web-guide.pdf', 'page_start': 1, 'page_end': 1}, {'document': 'operations-guide.pdf', 'page_start': 3, 'page_end': 3}] |
| safety-comparison-unsupported | combined/rrf/or | Compare what the customer manages in IaaS and the lunar habitat. (0.26398); Compare what the customer manages in IaaS (0.28087); Compare what the customer manages in the lunar habitat (0.24453) | False | False | False | [] |
| safety-semicolon-supported | combined/rrf/or | Where is application metadata stored; which algorithm stores password hashes? (0.45132); Where is application metadata stored (0.46045); which algorithm stores password hashes (0.45245) | False | False | False | [] |

## Decomposition, cost, and diagnostics

| Candidate | Intact/useful/neutral/harmful | Avg/max queries | Extra calls | Duplicate hits | Required pages recovered/lost | Budget affected |
|---|---|---|---|---|---|---|
| original/max/original | {'intact': 35} | 1.000/1 | 0 | 0 | 0/0 | [] |
| clauses/max/original | {'intact': 35} | 1.000/1 | 0 | 0 | 0/0 | [] |
| clauses/max/or | {'intact': 35} | 1.000/1 | 0 | 0 | 0/0 | [] |
| clauses/rrf/original | {'intact': 35} | 1.000/1 | 0 | 0 | 0/0 | [] |
| clauses/rrf/or | {'intact': 35} | 1.000/1 | 0 | 0 | 0/0 | [] |
| topics/max/original | {'intact': 34, 'harmful': 1} | 1.057/3 | 2 | 8 | 0/0 | [] |
| topics/max/or | {'intact': 34, 'harmful': 1} | 1.057/3 | 2 | 8 | 0/0 | [] |
| topics/rrf/original | {'intact': 34, 'neutral': 1} | 1.057/3 | 2 | 8 | 0/0 | [] |
| topics/rrf/or | {'intact': 34, 'neutral': 1} | 1.057/3 | 2 | 8 | 0/0 | [] |
| combined/max/original | {'intact': 34, 'harmful': 1} | 1.057/3 | 2 | 8 | 0/0 | [] |
| combined/max/or | {'intact': 34, 'harmful': 1} | 1.057/3 | 2 | 8 | 0/0 | [] |
| combined/rrf/original | {'intact': 34, 'neutral': 1} | 1.057/3 | 2 | 8 | 0/0 | [] |
| combined/rrf/or | {'intact': 34, 'neutral': 1} | 1.057/3 | 2 | 8 | 0/0 | [] |

## Distractor interpretation

- **useful supporting context** (safety-explicit-questions, clauses/max/or): storage-guide.pdf page 1, authentication-guide.pdf page 2. Independent subqueries directly target both required pages and strengthen their scores, but both were already included: no new required evidence recovered.
- **harmless related context** (safety-three-topics, topics/max/original): storage-guide.pdf page 1. Added local-PDF/MongoDB metadata storage material is related background. It does not supply missing cloud responsibility pages.
- **misleading distractor** (safety-explicit-questions, clauses/max/or): operations-guide.pdf page 2, operations-guide.pdf page 3. Idle dashboard sessions and automation API-key lifetimes are different from JWT access expiry; extra timers could be conflated.
- **misleading distractor** (safety-unsupported-rotation, topics/max/or): authentication-guide.pdf page 1, operations-guide.pdf page 3, web-guide.pdf page 1, web-guide.pdf page 2. Signature verification, automation-key expiry, CSRF, and response caching do not specify JWT rotation or emergency revocation. These are synthetic corpus judgments, not an LLM judge.

Full JSON contains per-query rankings, merged cosine and RRF scores, required-evidence discovery query indices, subquery contributions, and all failed adoption gates. No vectors or full corpus text are stored in this report.

## Limitations

A small, already-studied synthetic development benchmark cannot establish generalization. Seven false negatives are not clear independent multi-topic requests. These conservative rules intentionally do not rewrite them or introduce synonyms. Original-gate fusion cannot recover original-gate false negatives by construction. OR can erase unsupported intent by accepting one supported fragment. Top-five fusion can displace required evidence even when its union contains it. Extra non-required passages are a conservative distractor proxy, not a semantic harm score. No generated answers or entailment were judged; no LLM judge was used. Rounded cached scores are reproducible by replay; fresh inference may have small hardware/version numerical differences.

No Phase 15B architecture is recommended unless predeclared gates pass. Production settings, citations, history, search, and frontend remain unchanged.
