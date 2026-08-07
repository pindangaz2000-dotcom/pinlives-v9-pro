"""
Offline self-test of the per-site code filter, in the same shape the live
extract_and_report produces. Runs without Telegram, so the filtering can be
checked before a live run. Sample posts mimic real channel content: armoured
codes, Vietnamese marketing noise, links, and phone numbers.

    python -m pinlives_pro.tools.report_selftest
"""
from ..core.filters import extract_codes_for_site
from .extract_and_report import print_report

SAMPLES = {
    'C168 | PHÁT CODE MIỄN PHÍ': ('c168', [
        'GIFTCODE HOM NAY #eHiewhjJEX nhanh tay', 'Ma moi: k3nH8pQz2 dung truoc 22h']),
    'QQ88 - PHÁT CODE MIỄN PHÍ': ('qq88', [
        'QQ88 code: 7hK2mQ9z nhan ngay', 'lien he 0388588488', 'QQ88 tang 8fRd2Xy7 free']),
    'HI88 ĐẠI TIỆC': ('hi88', [
        'HI88 code: uRCzuDA8Z7 truy cap hi88.com', 'Ma tiep theo WY4rTtx4Ka']),
    'MB66 PHÁT CODE': ('mb66', [
        'MB66 GIFTCODE: aB★7x9◆Qm✦K nhanh!', 'code moi: xZ★3k9◆Pw✦M']),
    'F8BET NỔ HŨ': ('f8bet', [
        'F8BET code: X7f2Kq9Lm chao mung', 'F8BET M4nP8vT2q']),
}


def main() -> int:
    report = {}
    for name, (site, posts) in SAMPLES.items():
        codes = []
        for text in posts:
            codes.extend(extract_codes_for_site(text, site, source='text'))
        report[name] = {'site_id': site or '(none)', 'posts_read': len(posts),
                        'codes': sorted(set(codes))}
    print_report(report)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
