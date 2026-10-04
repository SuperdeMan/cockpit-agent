"""候选回答判据（端侧快路径据此把点选 / 说出的候选名送回云端挂起）。"""
from runtime.choice_answer import answers_offered_choice

OFFER = ["上海东方明珠广播电视塔", "东方·明珠城"]


def test_the_offered_name_itself_is_an_answer():
    assert answers_offered_choice("上海东方明珠广播电视塔", OFFER)
    assert answers_offered_choice("东方明珠城", OFFER)                  # 标点不算
    assert answers_offered_choice("东方明珠广播电视塔", OFFER)           # 候选里连续的一段，至少 4 个字
    assert answers_offered_choice("去上海东方明珠广播电视塔", OFFER)     # 候选外只多一两个字
    assert answers_offered_choice("上海东方明珠广播电视塔吧", OFFER)


def test_commands_and_short_fragments_are_not_answers():
    assert not answers_offered_choice("广播", OFFER)                     # 两个字仍是一条指令
    assert not answers_offered_choice("打开广播", OFFER)
    assert not answers_offered_choice("帮我打开上海东方明珠广播电视塔的介绍", OFFER)
    assert not answers_offered_choice("打开空调", OFFER)
    assert not answers_offered_choice("", OFFER)
    assert not answers_offered_choice("上海东方明珠广播电视塔", [])
    assert not answers_offered_choice("上海东方明珠广播电视塔", ["", "  "])
