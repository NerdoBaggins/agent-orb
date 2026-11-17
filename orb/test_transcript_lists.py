import asyncio

import pytest

from orb.test_transcript_speaker import transcript_page

pytestmark = pytest.mark.browser

pytest.importorskip("playwright.async_api")

TWELVE_ITEMS = "\n".join(f"{n}. item {n}" for n in range(1, 13))

OVERHANG = """(font) => {
  if (font) document.documentElement.style.setProperty('--orb-font', font);
  const reply = document.querySelector('#builtin-transcript .tr-row.reply .tr-a');
  const item = reply.querySelector('ol li:last-child');
  const style = getComputedStyle(item);
  const ruler = document.createElement('canvas').getContext('2d');
  ruler.font = `${style.fontSize} ${style.fontFamily}`;
  const marker = ruler.measureText('12. ').width;
  return reply.getBoundingClientRect().left - (item.getBoundingClientRect().left - marker);
}"""


@pytest.mark.timeout(60)
@pytest.mark.parametrize("font", [None, "'JetBrains Mono', monospace"], ids=["default font", "monospace theme font"])
def test_a_reply_list_of_two_digit_items_keeps_its_numbers_inside_the_reply(tmp_path, font):
    async def with_list(server):
        server.attached = True
        server.link.activity_state = {"busy": False, "doing": None, "session": "s1",
                                      "feed": [{"id": "r1", "kind": "reply", "text": TWELVE_ITEMS, "final": True}]}

    async def overhang(page, server):
        return await page.evaluate(OVERHANG, font)

    _, past_the_edge = asyncio.run(transcript_page(tmp_path, with_list, overhang))
    assert past_the_edge <= 0
