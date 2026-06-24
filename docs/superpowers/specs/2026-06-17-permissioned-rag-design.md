# 권한 인지형(Permission-aware) 문서 RAG 설계

작성일: 2026-06-17
상태: 설계 승인 대기 (Draft)
대상 시스템: 오프라인 한국어 문서 OCR 엔진 → Vector DB → MAX Chat RAG

---

## 1. 목표와 범위

부서 문서를 OCR해 Vector DB로 적재하고, 내부망 LLM 봇 **MAX Chat**이 그 DB를
근거로 답하도록 RAG를 구축한다. 핵심 제약은 **권한 관리**다 — 사용자는 자신이
접근 가능한 부서·보안등급·개인 자료의 범위 안에서만 검색·열람할 수 있어야 하며,
추후 다른 부서로 확장해도 **스키마·코드 변경 없이** 키 값만으로 격리가 유지돼야 한다.

추가로 사용자는 **자신의 문서를 직접 업로드**해 OCR→검증→벡터화→저장까지 셀프서비스로
수행할 수 있어야 하고, **문서 타입별로 다른 OCR 엔진을 선택**할 수 있어야 한다.

### 확정된 설계 결정 (브레인스토밍 합의)

| 항목 | 결정 |
|---|---|
| 권한 단위 | 부서 + 보안등급 (2축 ABAC) + 개인 소유(owner) = 실질 3축 |
| 신원 전달 | 사내 SSO/JWT. RAG API가 토큰 claims에서 권한 추출 (서버 강제) |
| 격리 방식 | 단일 테이블 + 서버 강제 메타데이터 사전필터 (+ RLS 2중 방어) |
| Vector DB | **pgvector (PostgreSQL)** — 기존 사내 PG 서버에 구축 |
| 임베딩 | **bge-m3 추천**(dense+sparse 단일 모델, 오프라인). 우리 문서로 벤치 후 확정 |
| 검색 | 사전필터 → Hybrid → Rerank → Parent-doc (단일 파이프라인) |
| 생성(LLM) | Azure OpenAI GPT. 전 보안등급 egress 허용(사내 승인됨) |
| Agentic RAG | 2-티어: 단발 기본 → 복잡 질의 시 멀티홉 승급 |
| 업로드 범위 | 업로드자가 개인/부서 선택. owner/dept는 SSO 신원으로 서버가 부여 |

---

## 2. 아키텍처 개요

두 개의 경로로 구성된다.

- **인제스션 경로(배치/셀프서비스, 전부 오프라인)**: 문서 → 엔진선택 → OCR →
  오타 검증 → 청킹·임베딩 → 권한 태깅 → pgvector upsert
- **질의 경로(실시간)**: 사용자(+SSO) → MAX Chat → RAG API(JWT 검증→권한 추출) →
  `retrieve()`(사전필터+Hybrid+Rerank+Parent) → Azure GPT 생성 → 인용 포함 답변

```
[인제스션]
부서/개인 문서 → 타입감지 → 엔진선택(레지스트리) → OCR → 검증·교정(HITL)
            → 청킹·임베딩(bge-m3) → 권한태깅(dept/clearance/owner) → pgvector upsert

[질의]
사용자+SSO → MAX Chat → RAG API ──JWT검증──> user_claims{dept,clearance,owner}
   → retrieve(query, user_claims):
        1) 사전필터(권한)  2) Hybrid(dense+BM25)  3) Rerank  4) Parent-doc 확장
   → Azure OpenAI GPT(컨텍스트+인용) → 답변
```

신뢰 경계: **권한 필터는 `retrieve()` 서비스 안에 고정**된다. MAX Chat, 에이전트
루프, 사용자 중 누구도 이 필터를 우회·변조할 수 없다.

---

## 3. 권한 모델 (핵심)

### 3.1 청크 행(row) 스키마

각 청크는 한 행(row)이며, 본문 벡터 컬럼과 함께 다음 권한·출처 메타데이터 컬럼을 갖는다.
dense는 `vector`/`halfvec`, sparse는 `sparsevec` 컬럼으로 저장한다.

| 필드 | 타입 | 의미 |
|---|---|---|
| `dept` | string[] | 소유 부서 코드(들). 다중 부서 공유 가능. 개인자료면 `[]` |
| `clearance` | int (1~3) | 보안등급. 1=일반, 2=대외비, 3=기밀 |
| `owner` | string \| null | 개인 소유자 ID. 부서 공유 문서면 `null` |
| `visibility` | "private" \| "dept" | 업로드 시 선택값(가독성/감사용 보조) |
| `doc_id` | string | 문서 식별자 |
| `page` | int | 페이지 번호 (인용용) |
| `source_path` | string | 원본 위치/파일명 (인용용) |
| `ingested_at` | timestamp | 적재 시각 |

`dept`, `clearance`, `owner` 컬럼에 인덱스(부서 배열은 GIN, 등급/owner는 B-tree)를 만들어
권한 필터링이 벡터 검색과 결합되게 한다. 벡터 컬럼에는 HNSW 인덱스를 건다.

### 3.2 서버 강제 필터 (질의 시)

RAG API는 JWT에서 사용자의 `dept_list`, `clearance`, `user_id`를 추출하고,
**클라이언트가 보낸 어떤 권한 값도 신뢰하지 않는다.** 검색 필터는 SQL `WHERE`의 OR 구조:

```sql
WHERE (
      (dept && :user_dept_list AND clearance <= :user_clearance)  -- 부서 공유 (배열 겹침)
   OR (owner = :user_id)                                          -- 내 개인 문서
)
```

→ 사용자는 (접근 가능 부서의 등급 이하 문서) ∪ (본인이 올린 개인 문서)만 받는다.
이 조건은 애플리케이션 쿼리뿐 아니라 **RLS 정책으로도 동일하게 강제**한다(§9) — 앱이
필터를 빠뜨려도 DB가 막는 2중 방어.

### 3.3 부서 확장 시나리오 (요구사항의 핵심)

새 부서 추가 = 그 부서 문서를 `dept=["newdept"]`로 태깅해 **같은 컬렉션에 적재**.
스키마/코드 변경 없음. 신규 사용자는 SSO claims에 부서가 포함되는 순간 자동으로
해당 부서 데이터만 보인다. 권한 부여/회수는 SSO·디렉터리 쪽 부서/등급 변경으로
일원화된다(Vector DB는 진실 원천이 아니라 그 투영).

---

## 4. 셀프서비스 인제스션

업로드 한 건을 **job 상태 머신**으로 모델링한다. OCR·임베딩이 수 초~수십 초
걸리고, 검증 단계에서 사람이 개입했다 재개해야 하기 때문이다.

```
queued → ocr → review → indexing → done
                  └────────────────────→ failed (재시도 가능)
```

### 4.1 OCR 엔진 레지스트리 (플러그인화)

엔진이 현재 하나여도 처음부터 공통 인터페이스 뒤에 둔다. 신규 엔진은 등록만으로
추가된다.

```python
class OcrEngine(Protocol):
    name: str
    handles: list[str]                  # 예: ["contract_pdf","table_heavy","scan_image"]
    def run(doc, opts) -> OcrResult      # 글자 + bbox + confidence + 페이지 레이아웃
```

- 레지스트리: 기존 `paddle`, `hybrid_vl`, `paddle_vl` 등을 등록. 추가는 `register(engine)`.
- **타입 감지 → 엔진 제안**: 업로드 시 문서 타입을 가볍게 판별해 추천 엔진을 기본값으로
  띄우고, 사용자가 드롭다운으로 override 가능. ("타입별 다른 엔진" + "사용자 선택" 동시 충족)
- 문서 OCR은 프로젝트 규칙대로 **레이아웃 ON**(`use_table=True`)이 기본값으로 강제된다.

### 4.2 오타 검증·교정 게이트 (Human-in-the-loop)

OCR 결과를 바로 벡터화하지 않는다. 검증 게이트를 둔다.

- 엔진의 **confidence** + 휴리스틱(기존 `postprocess.normalize_ocr_text`의 `₩` 정규화 등)으로
  저신뢰 토큰을 하이라이트.
- 사용자가 원문 이미지와 나란히 보며 수정 → 확정. 확정 텍스트만 다음 단계로.
- (선택, 나중) 멀티엔진 동시 실행 후 결과 diff로 불일치 지점을 오인식 후보로 제시. 비용↑라
  기본 비활성(YAGNI).

### 4.3 권한 태깅 (저장 시점)

`indexing` 단계에서 청크별 payload의 `dept`/`clearance`/`owner`를 **SSO 신원으로
서버가 부여**한다. 사용자는 "개인/부서"와 "등급"만 고르고, 실제 owner ID·부서 코드는
토큰에서 온다 — 자기 부서가 아닌 곳, 본인이 아닌 owner로 심을 수 없다.

---

## 5. 검색 파이프라인 (`retrieve`)

택1이 아니라 **하나의 요청이 4단계를 순차 통과**한다. 전부 로컬·오프라인.

1. **사전필터(Pre-filter) — 가장 중요.** §3.2의 권한 `WHERE` 절을 벡터 검색과 함께 적용해
   후보 공간을 권한 범위로 축소한다. post-filter(검색 후 거르기) 금지: top-k가 권한 밖 문서로
   채워져 볼 수 있는 문서가 누락되고 미세 유출 위험이 생긴다. pgvector에선 WHERE 절 + RLS가
   처리한다(§9).
2. **Hybrid search.** dense(bge-m3, HNSW) + sparse(bge-m3 `sparsevec` 또는 Postgres
   전문검색 `tsvector`)를 **SQL로 RRF 결합**해 30~50 후보. 한국어 문서의 정확 일치 토큰
   ("제3조", "₩1,250,000", 사번·부서명)에 강하다. **권한 필터는 두 leg 모두에 적용.**
3. **Rerank.** 로컬 cross-encoder 리랭커(bge-reranker 류)로 상위 5~8개 정밀 선별.
4. **Parent-doc(small-to-big).** 검색은 작은 청크로 하되, LLM엔 그 부모 블록(조항 전체·표
   전체)을 넣어 문맥 보존.

`retrieve(query, user_claims) -> list[ParentChunk]` 단일 서비스로 캡슐화. 권한 경계가
여기 고정되는 것이 보안의 핵심.

### 임베딩 결정
오프라인 한국어 멀티링궐 임베딩으로 **bge-m3 추천**(확정 아님 — 우리 문서로 벤치 후 확정).
dense와 sparse를 한 모델로 처리해 Hybrid 구성이 단순한 것이 선정 이유. 검토 후보: KURE(한국어
특화), multilingual-e5-large(경량). sparse leg는 bge-m3 sparse(`sparsevec`)와 Postgres
전문검색(`tsvector`, 한국어 형태소 확장) 중 벤치로 택일한다.

---

## 6. 생성 (Generation)

- 검색 결과 컨텍스트 + 인용 메타를 프롬프트로 조립 → **Azure OpenAI GPT 호출** → 출처
  표시 답변. 생성 자체는 단순 호출로 충분(품질 천장은 검색이 좌우).
- **Egress 정책: 전 보안등급 허용(사내 승인됨).** 벡터·임베딩·검색은 전부 내부에 남고, 최종
  컨텍스트 텍스트만 Azure로 전송된다. (정책이 바뀌면 등급별 차등/내부 모델 폴백을 §8 확장점에서 도입)

---

## 7. Agentic RAG (멀티홉·다단계 추론)

"여러 문서 종합·다단계 추론"을 지원한다. 안전 원칙: **권한 필터는 에이전트 루프가 아니라
그 아래 `retrieve()`에 고정**한다.

```
Agentic 오케스트레이터 (자체 개발, Azure GPT가 추론 담당)
   ├─ 계획: 질문을 하위질문으로 분해
   ├─ 루프(멀티홉): 필요한 만큼 retrieve(subq, user_claims) 반복 호출
   │                 (매 호출에 동일 user_claims → 권한 필터 자동 재적용)
   └─ 종합: 모은 근거로 최종 답 + 인용
```

에이전트가 몇 번을 재검색하든 어떤 하위질문을 만들든 권한 밖 청크를 물리적으로 가져올 수
없다(우회 표면 없음).

**2-티어 운영**: 기본은 단발(single-shot) RAG. 복잡 질의로 판단되면 agentic 멀티홉으로 승급.
멀티홉은 지연↑·Azure 호출/전송량↑이므로 항상 켜지 않는다.

질의 재작성·라우팅·승급 판단 등 고도화 질의 로직은 검색 앞단에서 자체 개발한다.

---

## 8. 컴포넌트 경계 (단위별 책임)

| 컴포넌트 | 책임 | 의존 |
|---|---|---|
| OCR 엔진 레지스트리 | 타입→엔진 매핑, 공통 `run()` 인터페이스 | 기존 엔진들 |
| 인제스션 서비스 | job 상태머신, 검증 게이트, 청킹·임베딩, 태깅, upsert | 레지스트리, 임베딩, pgvector, SSO |
| `retrieve()` 검색 서비스 | 사전필터+Hybrid+Rerank+Parent. **권한 경계** | pgvector, 임베딩, 리랭커 |
| RAG API | JWT 검증→claims, retrieve 호출, 생성 오케스트레이션 | SSO, retrieve, Azure GPT |
| Agentic 오케스트레이터 | 계획·멀티홉·종합 (티어2) | RAG API/retrieve, Azure GPT |

각 단위는 독립 테스트 가능해야 한다. 특히 **권한 필터 회귀 테스트**(권한 밖 문서가 절대
새지 않음)는 보안 임계 테스트로 필수.

---

## 9. 보안 실패 모드 & 가드레일

- **유일한 치명 위험 = 필터 누락/우회.** 단일 테이블이므로 필터가 빠지면 전 부서가 노출된다.
  → `retrieve()`는 user_claims 없이는 절대 검색하지 않도록 강제(타입/런타임 가드). 권한 누출
  통합 테스트를 CI 게이트로.
- **RLS(Row-Level Security) 2중 방어.** dept/clearance/owner 조건을 §3.2와 동일하게 RLS
  정책으로 DB에 건다. 세션마다 `SET app.user_id / app.dept_list / app.clearance`를 주입하고,
  앱 쿼리가 WHERE를 빠뜨려도 DB가 권한 밖 행을 반환하지 않게 한다. pgvector 채택의 핵심 이점.
- **클라이언트 권한 신뢰 금지.** dept/clearance/owner는 오직 검증된 JWT에서. 요청 본문의 권한
  값은 무시.
- **감사 로그.** 누가(user_id) 언제 어떤 질의로 어떤 doc_id를 받았는지 기록(권한 문서 접근
  추적·사후 감사).
- **인제스션 권한 위조 차단.** owner/dept는 업로더 토큰에서만 부여.

---

## 10. 보류/확장점 (YAGNI)

- Contextual Retrieval(청크별 LLM 맥락 부착): 워스트 페이지 품질 부족 시 도입.
- 멀티엔진 diff 자동 검증: 검증 부하 큰 문서에 한해.
- 등급별 생성 차등(기밀=내부 모델): egress 정책 변경 시.
- GraphRAG: 현 범위 밖.

---

## 11. 미해결/후속 확인

- **pgvector 확장 버전 확인**: `sparsevec`/`halfvec`는 0.7.0+, HNSW는 0.5.0+ 필요. 사내 PG에
  설치 여부·버전 확인. 미설치/구버전이면 업그레이드 또는 sparse leg를 `tsvector`로 대체.
- **규모 가드레일**: 부서 단위(수십만~수백만 청크)면 pgvector로 충분. **수천만 청크 이상**으로
  커지면 전용 ANN(Qdrant 등)으로의 이전을 재검토(인터페이스는 §8처럼 추상화돼 교체 가능).
- 임베딩 최종 확정: bge-m3 vs KURE vs e5 — 우리 문서로 검색 품질 벤치.
- SSO claims 스키마: 부서가 단일인지 다중인지, 등급 표현 방식(claims 필드명) 확정 필요.
- 리랭커 모델 후보 벤치(bge-reranker 등) — 오프라인 GPU 성능 측정.
