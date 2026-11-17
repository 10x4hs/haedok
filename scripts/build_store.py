"""저작권이 살아 있는 유명 도서의 정보·표지·알라딘 전자책 링크를 모아 app/data/store.json에 둔다.

본문은 없다. 앱은 표지와 정보를 보여 주고, 사는 곳(알라딘 전자책 상품 페이지)으로 연결한다.

이미 있는 책은 그대로 두고(= /store/<id> 주소가 바뀌지 않는다) 목록에 새로 넣은 책만 찾아 뒤에 붙인다.
실행: python scripts/build_store.py            (없는 책만 찾아 보탠다)
      python scripts/build_store.py --dry-run  (찾기만 하고 파일은 그대로 둔다)
"""

import asyncio
import html as htmllib
import json
import re
import sys
from pathlib import Path
from typing import NamedTuple

import httpx

OUT = Path(__file__).resolve().parent.parent / "app" / "data" / "store.json"
SEARCH = "https://www.aladin.co.kr/search/wsearchresult.aspx"
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15 Safari/605.1.15"}

# (검색어, 제목에 꼭 들어갈 말) — 많이 읽히는 순서대로
FAMOUS = [
    # 세계 현대 고전
    ("데미안 헤르만 헤세", "데미안"),
    ("수레바퀴 아래서 헤르만 헤세", "수레바퀴 아래서"),
    ("싯다르타 헤르만 헤세", "싯다르타"),
    ("동물농장 조지 오웰", "동물농장"),
    ("1984 조지 오웰", "1984"),
    ("어린 왕자 생텍쥐페리", "어린 왕자"),
    ("노인과 바다 헤밍웨이", "노인과 바다"),
    ("무기여 잘 있거라", "무기여 잘 있거라"),
    ("누구를 위하여 종은 울리나", "누구를 위하여 종은 울리나"),
    ("호밀밭의 파수꾼", "호밀밭의 파수꾼"),
    ("이방인 알베르 카뮈", "이방인"),
    ("페스트 알베르 카뮈", "페스트"),
    ("시지프 신화 카뮈", "시지프 신화"),
    ("변신 카프카", "변신"),
    ("인간 실격 다자이 오사무", "인간 실격"),
    ("설국 가와바타 야스나리", "설국"),
    ("멋진 신세계 올더스 헉슬리", "멋진 신세계"),
    ("화씨 451 레이 브래드버리", "화씨 451"),
    ("앵무새 죽이기 하퍼 리", "앵무새 죽이기"),
    ("향수 파트리크 쥐스킨트", "향수"),
    ("좀머 씨 이야기", "좀머 씨 이야기"),
    ("나의 라임 오렌지나무", "나의 라임 오렌지나무"),
    ("모모 미하엘 엔데", "모모"),
    ("끝없는 이야기 미하엘 엔데", "끝없는 이야기"),
    ("연금술사 파울로 코엘료", "연금술사"),
    ("백년의 고독 마르케스", "백년의 고독"),
    ("콜레라 시대의 사랑", "콜레라 시대의 사랑"),
    ("롤리타 나보코프", "롤리타"),
    ("파리대왕 윌리엄 골딩", "파리대왕"),
    ("시계태엽 오렌지", "시계태엽 오렌지"),
    ("앵무새 죽이기", "앵무새 죽이기"),
    ("분노의 포도 존 스타인벡", "분노의 포도"),
    ("에덴의 동쪽", "에덴의 동쪽"),
    ("생쥐와 인간 존 스타인벡", "생쥐와 인간"),
    ("위대한 개츠비 피츠제럴드", "위대한 개츠비"),
    ("빨강 머리 앤", "빨강 머리 앤"),
    ("어린 왕자", "어린 왕자"),
    ("갈매기의 꿈 리처드 바크", "갈매기의 꿈"),
    ("꽃들에게 희망을", "꽃들에게 희망을"),
    ("눈먼 자들의 도시 주제 사라마구", "눈먼 자들의 도시"),
    ("남아 있는 나날 가즈오 이시구로", "남아 있는 나날"),
    ("나를 보내지 마", "나를 보내지 마"),
    ("클라라와 태양", "클라라와 태양"),
    ("상실의 시대", "상실의 시대"),
    ("노르웨이의 숲 무라카미 하루키", "노르웨이의 숲"),
    ("해변의 카프카 무라카미 하루키", "해변의 카프카"),
    ("1Q84", "1Q84"),
    ("기사단장 죽이기", "기사단장 죽이기"),
    ("나미야 잡화점의 기적", "나미야 잡화점의 기적"),
    ("용의자 X의 헌신", "용의자 X의 헌신"),
    ("백야행 히가시노 게이고", "백야행"),
    ("키친 요시모토 바나나", "키친"),
    ("오만과 편견", "오만과 편견"),
    ("죄와 벌 도스토옙스키", "죄와 벌"),
    ("카라마조프가의 형제들", "카라마조프가의 형제들"),
    ("안나 카레니나", "안나 카레니나"),
    ("전쟁과 평화 톨스토이", "전쟁과 평화"),
    ("파우스트 괴테", "파우스트"),
    ("젊은 베르테르의 슬픔", "젊은 베르테르의 슬픔"),
    ("이반 일리치의 죽음", "이반 일리치의 죽음"),
    ("빨강의 자서전", "빨강의 자서전"),
    ("미드나잇 라이브러리", "미드나잇 라이브러리"),
    ("가재가 노래하는 곳", "가재가 노래하는 곳"),
    ("우리가 빛의 속도로 갈 수 없다면", "우리가 빛의 속도로 갈 수 없다면"),
    ("프로젝트 헤일메리", "프로젝트 헤일메리"),
    ("마션 앤디 위어", "마션"),
    ("해리 포터와 마법사의 돌", "해리 포터와 마법사의 돌"),
    ("반지의 제왕", "반지의 제왕"),
    ("나니아 연대기", "나니아 연대기"),
    ("샬롯의 거미줄", "샬롯의 거미줄"),
    ("마틸다 로알드 달", "마틸다"),
    ("찰리와 초콜릿 공장", "찰리와 초콜릿 공장"),
    # 한국 현대 문학
    ("채식주의자 한강", "채식주의자"),
    ("소년이 온다 한강", "소년이 온다"),
    ("작별하지 않는다 한강", "작별하지 않는다"),
    ("흰 한강", "흰"),
    ("희랍어 시간 한강", "희랍어 시간"),
    ("바람이 분다, 가라 한강", "바람이 분다"),
    ("아몬드 손원평", "아몬드"),
    ("불편한 편의점 김호연", "불편한 편의점"),
    ("달러구트 꿈 백화점", "달러구트 꿈 백화점"),
    ("82년생 김지영", "82년생 김지영"),
    ("칼의 노래 김훈", "칼의 노래"),
    ("남한산성 김훈", "남한산성"),
    ("난장이가 쏘아올린 작은 공", "난장이가 쏘아올린 작은 공"),
    ("무진기행 김승옥", "무진기행"),
    ("토지 박경리", "토지"),
    ("엄마를 부탁해 신경숙", "엄마를 부탁해"),
    ("살인자의 기억법 김영하", "살인자의 기억법"),
    ("검은 꽃 김영하", "검은 꽃"),
    ("빛의 제국 김영하", "빛의 제국"),
    ("시선으로부터 정세랑", "시선으로부터"),
    ("보건교사 안은영", "보건교사 안은영"),
    ("피프티 피플", "피프티 피플"),
    ("고래 천명관", "고래"),
    ("파과 구병모", "파과"),
    ("완득이 김려령", "완득이"),
    ("소년을 위로해줘", "소년을 위로해줘"),
    ("구의 증명 최진영", "구의 증명"),
    ("쇼코의 미소 최은영", "쇼코의 미소"),
    ("밝은 밤 최은영", "밝은 밤"),
    ("지구에서 한아뿐", "지구에서 한아뿐"),
    ("저주토끼 정보라", "저주토끼"),
    ("천 개의 파랑", "천 개의 파랑"),
    ("하얼빈 김훈", "하얼빈"),
    ("모순 양귀자", "모순"),
    ("나의 아름다운 정원 심윤경", "나의 아름다운 정원"),
    ("어린 왕자", "어린 왕자"),
    ("서울 안과 밖 그 사이의 마음들", "서울 안과 밖"),
    ("이처럼 사소한 것들", "이처럼 사소한 것들"),
    ("급류 정대건", "급류"),
    ("세상의 마지막 기차역", "세상의 마지막 기차역"),
    ("메리골드 마음 세탁소", "메리골드 마음 세탁소"),
    ("어서 오세요, 휴남동 서점입니다", "어서 오세요, 휴남동 서점입니다"),
    # 에세이·인문·교양
    ("사피엔스 유발 하라리", "사피엔스"),
    ("호모 데우스", "호모 데우스"),
    ("총 균 쇠", "총 균 쇠"),
    ("코스모스 칼 세이건", "코스모스"),
    ("이기적 유전자 리처드 도킨스", "이기적 유전자"),
    ("정의란 무엇인가 마이클 샌델", "정의란 무엇인가"),
    ("죽음의 수용소에서 빅터 프랭클", "죽음의 수용소에서"),
    ("미움받을 용기", "미움받을 용기"),
    ("데일 카네기 인간관계론", "데일 카네기 인간관계론"),
    ("아주 작은 습관의 힘", "아주 작은 습관의 힘"),
    ("돈의 속성 김승호", "돈의 속성"),
    ("부의 추월차선", "부의 추월차선"),
    ("역행자 자청", "역행자"),
    ("세이노의 가르침", "세이노의 가르침"),
    ("물고기는 존재하지 않는다", "물고기는 존재하지 않는다"),
    ("지적 대화를 위한 넓고 얕은 지식", "지적 대화를 위한 넓고 얕은 지식"),
    ("언어의 온도 이기주", "언어의 온도"),
    ("나는 나로 살기로 했다", "나는 나로 살기로 했다"),
    ("트렌드 코리아", "트렌드 코리아"),
    ("도둑맞은 집중력", "도둑맞은 집중력"),
    ("팩트풀니스", "팩트풀니스"),
    ("생각에 관한 생각 대니얼 카너먼", "생각에 관한 생각"),
    ("넛지", "넛지"),
    ("사람은 무엇으로 사는가", "사람은 무엇으로 사는가"),
    ("어떻게 살 것인가 유시민", "어떻게 살 것인가"),
    ("나는 왜 이 일을 하는가", "나는 왜 이 일을 하는가"),
    ("원씽", "원씽"),
    ("우리는 모두 죽는다는 것을 기억하라", "우리는 모두 죽는다는 것을 기억하라"),
    ("숨결이 바람 될 때", "숨결이 바람 될 때"),
    ("모든 순간이 너였다", "모든 순간이 너였다"),
]

M = "민음사"
# 더 보탠 유명한 책: (검색어, 제목에 꼭 들어갈 말, 지은이 이름의 일부, 먼저 고를 출판사)
# 지은이를 함께 확인해서 같은 제목의 해설서·요약본·다른 책이 섞이지 않게 한다.
MORE = [
    # 예전 목록의 책 가운데 지은이까지 확인해 바로잡은 것
    ("죽음의 수용소에서 빅터 프랭클", "빅터 프랭클의 죽음의 수용소에서", "프랭클", None),
    # 민음사 세계문학전집에서 많이 읽히는 고전
    ("폭풍의 언덕", "폭풍의 언덕", "브론테", M),
    ("제인 에어", "제인 에어", "브론테", M),
    ("위대한 유산", "위대한 유산", "디킨스", M),
    ("햄릿", "햄릿", "셰익스피어", M),
    ("맥베스", "맥베스", "셰익스피어", M),
    ("리어 왕", "리어 왕", "셰익스피어", M),
    ("오셀로", "오셀로", "셰익스피어", M),
    ("로미오와 줄리엣", "로미오와 줄리엣", "셰익스피어", M),
    ("돈키호테", "돈키호테", "세르반테스", M),
    ("레 미제라블", "레 미제라블", "위고", M),
    ("그리스인 조르바", "그리스인 조르바", "카잔", M),
    ("참을 수 없는 존재의 가벼움", "참을 수 없는 존재의 가벼움", "쿤데라", M),
    ("농담 밀란 쿤데라", "농담", "쿤데라", M),
    ("고도를 기다리며", "고도를 기다리며", "베케트", M),
    ("달과 6펜스", "달과 6펜스", "서머싯", M),
    ("마의 산", "마의 산", "토마스 만", M),
    ("양철북", "양철북", "그라스", M),
    ("오이디푸스 왕", "오이디푸스 왕", "소포클레스", M),
    ("신곡 단테", "신곡", "단테", M),
    ("걸리버 여행기", "걸리버 여행기", "스위프트", M),
    ("모비 딕", "모비 딕", "멜빌", None),
    ("허클베리 핀의 모험", "허클베리 핀의 모험", "트웨인", M),
    ("두 도시 이야기", "두 도시 이야기", "디킨스", None),
    ("크리스마스 캐럴", "크리스마스 캐럴", "디킨스", None),
    ("드라큘라", "드라큘라", "스토커", None),
    ("프랑켄슈타인", "프랑켄슈타인", "셸리", None),
    ("지킬 박사와 하이드", "지킬 박사와 하이드", "스티븐슨", None),
    ("보물섬", "보물섬", "스티븐슨", None),
    ("도리언 그레이의 초상", "도리언 그레이의 초상", "와일드", M),
    ("이상한 나라의 앨리스", "이상한 나라의 앨리스", "캐럴", None),
    ("적과 흑", "적과 흑", "스탕달", M),
    ("마담 보바리", "마담 보바리", "플로베르", M),
    ("부활 톨스토이", "부활", "톨스토이", M),
    ("지하로부터의 수기", "지하로부터의 수기", "도스토", M),
    ("이반 데니소비치, 수용소의 하루", "이반 데니소비치", "솔제니친", M),
    ("체호프 단편선", "체호프 단편선", "체호프", M),
    ("나르치스와 골드문트", "나르치스와 골드문트", "헤세", M),
    ("소송 카프카", "소송", "카프카", None),
    ("젊은 예술가의 초상", "젊은 예술가의 초상", "조이스", M),
    ("자기만의 방", "자기만의 방", "울프", M),
    ("장미의 이름", "장미의 이름", "에코", None),
    ("자기 앞의 생", "자기 앞의 생", "아자르", None),
    ("몬테크리스토 백작", "몬테크리스토 백작", "뒤마", M),
    ("80일간의 세계 일주", "80일간의 세계 일주", "베른", None),
    ("마음 나쓰메 소세키", "마음", "소세키", None),
    ("나는 고양이로소이다", "나는 고양이로소이다", "소세키", None),
    ("허삼관 매혈기", "허삼관 매혈기", "위화", None),
    ("인생 위화", "인생", "위화", None),
    ("스토너", "스토너", "윌리엄스", None),
    ("그리고 아무도 없었다", "그리고 아무도 없었다", "크리스티", None),
    ("오리엔트 특급 살인", "오리엔트 특급 살인", "크리스티", None),
    ("작은 아씨들", "작은 아씨들", "루이자", None),
    ("키다리 아저씨", "키다리 아저씨", "웹스터", None),
    ("오즈의 마법사", "오즈의 마법사", "바움", None),
    ("호빗", "호빗", "톨킨", None),
    ("듄 프랭크 허버트", "듄", "허버트", None),
    ("삼체 류츠신", "삼체", "류츠신", None),
    # 세계 현대 소설 베스트셀러
    ("개미 베르나르 베르베르", "개미", "베르베르", None),
    ("창문 넘어 도망친 100세 노인", "창문 넘어 도망친 100세 노인", "요나손", None),
    ("오베라는 남자", "오베라는 남자", "배크만", None),
    ("연을 쫓는 아이", "연을 쫓는 아이", "호세이니", None),
    ("파이 이야기", "파이 이야기", "마텔", None),
    ("헝거게임 수잔 콜린스", "헝거게임 -", "콜린스", None),
    ("죽은 시인의 사회", "죽은 시인의 사회", "클라인바움", None),
    ("유혹하는 글쓰기", "유혹하는 글쓰기", "스티븐 킹", None),
    ("도시와 그 불확실한 벽", "도시와 그 불확실한 벽", "하루키", None),
    ("편의점 인간", "편의점 인간", "무라타", None),
    # 한국 소설
    ("태백산맥 1 조정래", "태백산맥", "조정래", None),
    ("아리랑 1 조정래", "아리랑", "조정래", None),
    ("당신들의 천국", "당신들의 천국", "이청준", None),
    ("우리들의 일그러진 영웅", "우리들의 일그러진 영웅", "이문열", None),
    ("그 많던 싱아는 누가 다 먹었을까", "그 많던 싱아는 누가 다 먹었을까", "박완서", None),
    ("외딴방 신경숙", "외딴방", "신경숙", None),
    ("7년의 밤", "7년의 밤", "정유정", None),
    ("두근두근 내 인생", "두근두근 내 인생", "김애란", None),
    ("바깥은 여름", "바깥은 여름", "김애란", None),
    ("아버지의 해방일지", "아버지의 해방일지", "정지아", None),
    ("H마트에서 울다", "H마트에서 울다", "자우너", None),
    ("우리들의 행복한 시간", "우리들의 행복한 시간", "공지영", None),
    ("도가니 공지영", "도가니", "공지영", None),
    ("마당을 나온 암탉", "마당을 나온 암탉", "황선미", None),
    ("몽실 언니", "몽실 언니", "권정생", None),
    ("연어 안도현", "연어", "안도현", None),
    ("죽이고 싶은 아이", "죽이고 싶은 아이", "이꽃님", None),
    ("페인트 이희영", "페인트", "이희영", None),
    ("긴긴밤", "긴긴밤", "루리", None),
    ("위저드 베이커리", "위저드 베이커리", "구병모", None),
    ("내게 무해한 사람", "내게 무해한 사람", "최은영", None),
    ("대도시의 사랑법", "대도시의 사랑법", "박상영", None),
    ("일의 기쁨과 슬픔", "일의 기쁨과 슬픔", "장류진", None),
    ("지구 끝의 온실", "지구 끝의 온실", "김초엽", None),
    ("눈물을 마시는 새 1", "눈물을 마시는 새", "이영도", None),
    ("바리데기 황석영", "바리데기", "황석영", None),
    ("작별인사 김영하", "작별인사", "김영하", None),
    ("여행의 이유", "여행의 이유", "김영하", None),
    ("입 속의 검은 잎", "입 속의 검은 잎", "기형도", None),
    # 인문·과학·자기계발
    ("데일 카네기 자기관리론", "데일 카네기 자기관리론", "카네기", None),
    ("돈의 심리학", "돈의 심리학", "하우절", None),
    ("부자 아빠 가난한 아빠 1", "부자 아빠 가난한 아빠", "기요사키", None),
    ("월든 소로", "월든", "소로", None),
    ("군주론 마키아벨리", "군주론", "마키아벨리", None),
    ("차라투스트라는 이렇게 말했다", "차라투스트라는 이렇게 말했다", "니체", M),
    ("침묵의 봄", "침묵의 봄", "카슨", None),
    ("종의 기원 찰스 다윈", "종의 기원", "다윈", None),
    ("국가 플라톤", "국가", "플라톤", None),
    ("소크라테스의 변명", "소크라테스의 변명", "플라톤", None),
    ("명상록 마르쿠스 아우렐리우스", "명상록", "아우렐리우스", None),
    ("논어 공자", "논어", "공자", None),
    ("손자병법 손무", "손자병법", "손", None),
    ("자유론 존 스튜어트 밀", "자유론", "밀", None),
    ("사랑의 기술", "사랑의 기술", "프롬", None),
    ("역사란 무엇인가", "역사란 무엇인가", "카", None),
    ("백범일지", "백범일지", "김구", None),
    ("나의 문화유산답사기 1", "나의 문화유산답사기", "유홍준", None),
    ("성공하는 사람들의 7가지 습관", "성공하는 사람들의 7가지 습관", "코비", None),
    ("설득의 심리학 1", "설득의 심리학", "치알디니", None),
    ("그릿", "그릿", "더크워스", None),
    ("타이탄의 도구들", "타이탄의 도구들", "페리스", None),
    ("시크릿 론다 번", "The Secret 시크릿", "론다 번", None),
    ("미라클모닝 할 엘로드", "미라클모닝", "엘로드", None),
    ("아웃라이어", "아웃라이어", "글래드웰", None),
    ("죽고 싶지만 떡볶이는 먹고 싶어", "죽고 싶지만 떡볶이는 먹고 싶어", "백세희", None),
    ("멈추면, 비로소 보이는 것들", "멈추면, 비로소 보이는 것들", "혜민", None),
    ("공정하다는 착각", "공정하다는 착각", "샌델", None),
    ("불안 알랭 드 보통", "불안", "보통", None),
    ("지리의 힘", "지리의 힘", "마샬", None),
]


class Pick(NamedTuple):
    query: str
    must: str
    author: str | None = None  # 지은이 이름에 들어 있어야 하는 말
    publisher: str | None = None  # 이 출판사 판이 있으면 그것을 먼저 고른다


PICKS = [Pick(*p) for p in FAMOUS + MORE]

_BOX = re.compile(r'<div class="ss_book_box" itemId="(\d+)">(.*?)(?=<div class="ss_book_box"|$)', re.S)
_COVER = re.compile(r'<img src="(https://image\.aladin\.co\.kr/product/[^"]+?/cover\d*/[^"]+?\.(?:jpg|png))"')
_TITLE = re.compile(r'class="bo3">([^<]+)</a>')
# 옮긴이·그린이가 아니라 지은이
_AUTHOR = re.compile(r'AuthorSearch=[^"]+"[^>]*>([^<]+)</a>(?:\s|&nbsp;)*\((?:지은이|글|원작|저자)\)')
_ANY_AUTHOR = re.compile(r'AuthorSearch=[^"]+"[^>]*>([^<]+)</a>')
_PUBLISHER = re.compile(r'PublisherSearch=[^"]+"[^>]*>([^<]+)</a>', re.I)
_META_AUTHOR = re.compile(r'<meta name="author" content="([^",]+)')
PRODUCT = "https://www.aladin.co.kr/shop/wproduct.aspx"
_PRICE = re.compile(r'<span class="ss_p2"><em>([\d,]+)</em>원')
_KIND = re.compile(r'class="tit_category">\[([^\]]+)\]')  # [전자책] · [오디오북]
# 본책이 아닌 것: 맛보기·묶음·따라 쓰기·요약·해설·낭독 등
_NOT_THE_BOOK = re.compile(
    r"체험판|맛보기|미리보기|세트|필사|워크북|요약|해설|오디오북|컬러링|만화|읽기|논술|큰글자|\(영문판\)|원서"
)
# 제목 뒤에 붙은 권 표시가 2권 이후(2, 3 …, 중, 하)이면 첫 권이 아니다
_LATER_VOLUME = re.compile(
    r"^\s*[-–]?\s*[\(\[]?\s*(?:제\s*)?(?:[2-9]|\d{2,})\s*(?:권|부)?\s*[\)\]]?(?:\s|$|:|-)"
    r"|^\s*[-–]?\s*[\(\[]?\s*[중하]\s*[\)\]]?\s*$"
)
_FIRST_VOLUME = re.compile(r"^\s*[-–]?\s*[\(\[]?\s*(?:제\s*)?(?:1\s*(?:권|부)?|상)\s*[\)\]]?\s*$")
RETRIES = 3
PAUSE = 0.7  # 알라딘 서버 배려: 한 번에 하나씩, 사이에 쉰다


def _clean(s: str) -> str:
    return htmllib.unescape(re.sub(r"\s+", " ", s)).strip()


def candidates(page: str) -> list[dict]:
    """검색 결과 한 쪽에서 책 상자를 읽는다(나온 순서대로)."""
    out = []
    for item_id, block in _BOX.findall(page):
        t, kind = _TITLE.search(block), _KIND.search(block)
        if not t or (kind and kind.group(1) != "전자책"):  # 오디오북은 읽는 책이 아니다
            continue
        cover = _COVER.search(block)
        author = _AUTHOR.search(block)
        publisher = _PUBLISHER.search(block)
        price = _PRICE.search(block)
        out.append(
            {
                "id": item_id,
                "title": _clean(t.group(1)),
                "author": _clean(author.group(1)) if author else None,
                "cover": re.sub(r"/cover\d*/", "/cover500/", cover.group(1)) if cover else None,
                "price": int(price.group(1).replace(",", "")) if price else None,
                "url": f"https://www.aladin.co.kr/shop/wproduct.aspx?ItemId={item_id}&start=pebook",
                "_names": [_clean(n) for n in _ANY_AUTHOR.findall(block)],
                "_publisher": _clean(publisher.group(1)) if publisher else None,
            }
        )
    return out


def _fits(book: dict, pick: Pick) -> bool:
    title = book["title"]
    if not title.startswith(pick.must):  # 해설서·연구서 제외
        return False
    rest = title[len(pick.must) :]
    if _NOT_THE_BOOK.search(rest) or _LATER_VOLUME.search(rest):
        return False
    # 지은이 표시가 없는 책(옮긴이만 나오는 책)은 상품 페이지에서 따로 확인한다
    if pick.author and book["author"] and not any(pick.author in n for n in book["_names"]):
        return False
    return True


def choose(found: list[dict], pick: Pick) -> dict | None:
    """조건에 맞는 책 가운데 하나: 바라는 출판사 → 제목이 딱 맞는 것 → 첫 권 → 검색 순서."""
    fits = [b for b in found if _fits(b, pick)]
    if not fits:
        return None

    def rank(item: tuple[int, dict]) -> tuple[int, int, int]:
        i, b = item
        rest = b["title"][len(pick.must) :]
        exact = 0 if not rest.strip() else 1 if _FIRST_VOLUME.search(rest) else 2
        return (0 if pick.publisher and b["_publisher"] == pick.publisher else 1, exact, i)

    return min(enumerate(fits), key=rank)[1]


async def fetch(client: httpx.AsyncClient, url: str, params: dict) -> str:
    """잠깐의 오류·차단은 쉬었다가 다시 시도한다."""
    for attempt in range(RETRIES):
        try:
            r = await client.get(url, params=params)
            if r.status_code == 200:
                return r.text
        except httpx.HTTPError:
            if attempt == RETRIES - 1:
                raise
        await asyncio.sleep(2 + attempt * 3)
    return ""


async def find(client: httpx.AsyncClient, pick: Pick) -> dict | None:
    # 바라는 출판사가 있으면 그 이름을 넣어 먼저 찾고, 없으면 제목만으로 다시 찾는다
    queries = [f"{pick.query} {pick.publisher}", pick.query] if pick.publisher else [pick.query]
    for n, query in enumerate(queries):
        if n:
            await asyncio.sleep(PAUSE)
        page = await fetch(client, SEARCH, {"SearchTarget": "eBook", "SearchWord": query})
        book = choose(candidates(page), pick)
        if book is None:
            continue
        if not book["author"]:  # 검색 결과에 옮긴이만 나오는 책(향수 등)은 상품 페이지에서 지은이를 읽는다
            detail = await fetch(client, PRODUCT, {"ItemId": book["id"]})
            author = _AUTHOR.search(detail) or _META_AUTHOR.search(detail)
            book["author"] = _clean(author.group(1)) if author else None
            if pick.author and pick.author not in (book["author"] or ""):
                continue  # 지은이를 확인할 수 없는 책은 넣지 않는다
        return {k: v for k, v in book.items() if not k.startswith("_")}
    return None


def present(books: list[dict], pick: Pick) -> bool:
    """이미 실린 책인지: 제목이 같은 말로 시작하고, 지은이를 아는 경우 지은이도 맞아야 한다."""
    return any(
        b["title"].startswith(pick.must) and (not pick.author or pick.author in (b.get("author") or ""))
        for b in books
    )


async def main(dry_run: bool = False) -> None:
    books: list[dict] = json.loads(OUT.read_text()) if OUT.exists() else []
    seen = {b["id"] for b in books}
    before, missing = len(books), []
    async with httpx.AsyncClient(headers=UA, timeout=20, follow_redirects=True) as client:
        for pick in PICKS:
            if present(books, pick):
                continue
            try:
                book = await find(client, pick)
            except httpx.HTTPError:
                book = None
            if book and book["id"] not in seen:
                seen.add(book["id"])
                books.append(book)
                print("✓", pick.query, "→", book["id"], book["title"], "/", book["author"])
            else:
                missing.append(pick.query)
                print("✗", pick.query)
            await asyncio.sleep(PAUSE)
    if not dry_run:
        OUT.write_text(json.dumps(books, ensure_ascii=False, indent=1) + "\n")
    print(f"→ {OUT} ({before} → {len(books)}권, 못 찾은 책 {len(missing)}권)")
    if missing:
        print("못 찾은 책:", " | ".join(missing))


if __name__ == "__main__":
    asyncio.run(main(dry_run="--dry-run" in sys.argv))
