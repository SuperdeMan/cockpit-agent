# 意图路由评测基线 — routing

生成时间：2026-07-29T08:52:00.532384+00:00　commit：b979955

## 总览
| 分桶 | 总数 | 通过 | 通过率 |
|---|---|---|---|
| n1 | 192 | 189 | 98.4% |
| domain_base | 120 | 79 | 65.8% |
| **合计** | **312** | **268** | **85.9%** |

## 失败用例
- [n1] `麒麟电池和4680电池有什么区别` — expected=['info'] actual=['chitchat']
- [n1] `最近有什么好看的电影上映` — expected=['info'] actual=['nearby']
- [n1] `车道保持辅助怎么打开` — expected=['chitchat', 'manual'] actual=['lane_assistance']
- [domain_base] `第一页` — expected='base' actual='unknown'
- [domain_base] `取消一下` — expected='base' actual='information'
- [domain_base] `倒数第2页` — expected='base' actual='unknown'
- [domain_base] `重播` — expected='base' actual='media'
- [domain_base] `重新播放` — expected='base' actual='media'
- [domain_base] `重新播放看看` — expected='base' actual='media'
- [domain_base] `确认一下` — expected='base' actual='unknown'
- [domain_base] `乘客区切上一个` — expected='base' actual='media'
- [domain_base] `取消` — expected='base' actual='unknown'
- [domain_base] `取消订阅一下` — expected='base' actual='unknown'
- [domain_base] `退一退` — expected='base' actual='media'
- [domain_base] `请收藏取消` — expected='base' actual='unknown'
- [domain_base] `航班查询` — expected='information' actual='base'
- [domain_base] `大后年的火车票查询` — expected='information' actual='base'
- [domain_base] `请将联系人的号码查一下` — expected='information' actual='base'
- [domain_base] `请把火车票查询一下` — expected='information' actual='base'
- [domain_base] `关电视` — expected='media' actual='setting'
- [domain_base] `请将乘客区USB视频关闭` — expected='media' actual='setting'
- [domain_base] `乘坐区切上一张图片` — expected='media' actual='unknown'
- [domain_base] `把司机区和乘客区的多媒体图片给打开` — expected='media' actual='navi'
- [domain_base] `播电台主播的国家台电台` — expected='media' actual='unknown'
- [domain_base] `查询一下200以内的度假村` — expected='navi' actual='information'
- [domain_base] `查询中山路的酒店` — expected='navi' actual='information'
- [domain_base] `路线偏好设置为不走高速` — expected='navi' actual='base'
- [domain_base] `查询一下4分以上的度假村` — expected='navi' actual='information'
- [domain_base] `请你把到美亚光电还有多久告诉我` — expected='navi' actual='base'
- [domain_base] `当前限速多少` — expected='navi' actual='base'
- [domain_base] `打开车辆模式设置` — expected='setting' actual='unknown'
- [domain_base] `打开静音` — expected='setting' actual='media'
- [domain_base] `加入下队列看看` — expected='setting' actual='base'
- [domain_base] `关闭时间设置界面看看` — expected='setting' actual='base'
- [domain_base] `预测型碰撞报警系统等级调一调最高` — expected='setting' actual='base'
- [domain_base] `关闭免唤醒词界面看看` — expected='setting' actual='base'
- [domain_base] `请把WiFi关闭一下` — expected='setting' actual='base'
- [domain_base] `购买流量` — expected='setting' actual='base'
- [domain_base] `湿度查询` — expected='weather' actual='base'
- [domain_base] `查一查1日的风向` — expected='weather' actual='information'
- [domain_base] `查一下晚上10点的气象如何` — expected='weather' actual='information'
- [domain_base] `查询一下台北市花地玛堂区大后年的湿度` — expected='weather' actual='information'
- [domain_base] `查一下广州12月13日气象如何` — expected='weather' actual='information'
- [domain_base] `查询一下澳门特别行政区1日湿度` — expected='weather' actual='information'

## 数据来源
| 来源 | 用例数 |
|---|---|
| eval_corpus + skills golden | 192 |
| feishu_intents_full.jsonl(sampled) | 120 |


## 分域混淆矩阵

```

  期望\实际         charging         chitchat             hvac             info  lane_assistance           manual       navigation           nearby         reminder         research            scene             trip           vision
  charging                8                0                0                0                0                0                0                0                0                0                0                0                0   (8/8 = 100%)
  chitchat                0               33                0                1                1                0                0                0                0                0                0                0                0   (33/35 = 94%)
  hvac                    0                0                1                0                0                0                0                0                0                0                0                0                0   (1/1 = 100%)
  info                    0                1                1               61                0                0                0                1                0                1                0                0                0   (61/65 = 94%)
  manual                  0                0                0                0                0                1                0                0                0                0                0                0                0   (1/1 = 100%)
  navigation              0                0                0                0                0                0                4                0                0                0                0                0                0   (4/4 = 100%)
  nearby                  0                0                0                0                0                0                0                7                0                0                0                0                0   (7/7 = 100%)
  reminder                0                0                0                0                0                0                0                0               20                0                0                0                0   (20/20 = 100%)
  research                0                0                0                0                0                0                0                0                0               31                0                0                0   (31/31 = 100%)
  scene                   0                0                0                0                0                0                0                0                0                0               11                0                0   (11/11 = 100%)
  trip                    0                0                0                0                0                0                0                0                0                0                0                5                0   (5/5 = 100%)
  vision                  0                0                0                0                0                0                0                0                0                0                0                0                4   (4/4 = 100%)
```
