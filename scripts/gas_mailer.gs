/**
 * 해독 메일 발송기 — Google Apps Script 웹 앱.
 * 도메인 없이 이 Gmail 계정으로 누구에게나 인증 코드를 보낸다 (일반 Gmail 하루 100명).
 *
 * 설치:
 * 1. https://script.google.com 에서 새 프로젝트를 만들고 이 파일 내용을 붙여 넣는다.
 * 2. 프로젝트 설정 → 스크립트 속성에 SECRET = (긴 무작위 문자열) 을 넣는다.
 * 3. 배포 → 새 배포 → 웹 앱. 실행 계정 "나", 액세스 "모든 사용자". 권한을 허용한다.
 * 4. 웹 앱 URL을 Vercel 환경변수 GAS_MAIL_URL, 같은 SECRET을 GAS_MAIL_SECRET 에 넣는다.
 */
function doPost(e) {
  var out = function (obj) {
    return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
  };
  try {
    var req = JSON.parse(e.postData.contents);
    var secret = PropertiesService.getScriptProperties().getProperty('SECRET');
    if (!secret || req.secret !== secret) return out({ ok: false, error: 'bad secret' });
    if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(req.to || '')) return out({ ok: false, error: 'bad to' });
    MailApp.sendEmail({
      to: req.to,
      subject: String(req.subject || '').slice(0, 200),
      body: String(req.text || ''),
      htmlBody: String(req.html || ''),
      name: String(req.name || '해독'),
    });
    return out({ ok: true, remaining: MailApp.getRemainingDailyQuota() });
  } catch (err) {
    return out({ ok: false, error: String(err) });
  }
}
