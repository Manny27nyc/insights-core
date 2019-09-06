from insights.parsers.yumlog import YumLog
from insights import add_filter
from insights.specs import Specs
from insights.tests import context_wrap

LOGINFO = """
May 23 18:06:24 Installed: wget-1.14-10.el7_0.1.x86_64
Jan 24 00:24:00 Updated: glibc-2.12-1.149.el6_6.4.x86_64
Jan 24 00:24:09 Updated: glibc-devel-2.12-1.149.el6_6.4.x86_64
Jan 24 00:24:10 Updated: nss-softokn-3.14.3-19.el6_6.x86_64
Jan 24 18:10:05 Updated: 1:openssl-libs-1.0.1e-51.el7_2.5.x86_64
Jan 24 00:24:11 Updated: glibc-2.12-1.149.el6_6.4.i686
May 23 16:09:09 Erased: redhat-access-insights-batch
May 23 16:09:09 Erased: katello-agent
Jan 24 00:24:11 Updated: glibc-devel-2.12-1.149.el6_6.4.i686
""".strip()

add_filter(Specs.messages, [
    "Installed",
    "Updated",
    "Erased",
])


def test_messages():
    log_info = YumLog(context_wrap(LOGINFO))
    bona_list = log_info.get('Installed')
    assert 1 == len(bona_list)
    assert bona_list[0].get('raw_message') == "May 23 18:06:24 Installed: wget-1.14-10.el7_0.1.x86_64"
    crond = log_info.get('Erased')
    assert 2 == len(crond)
