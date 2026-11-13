# notam_import

FAA NOTAM Search の Archive Search の書き出し(fnsNotams_*.xls)を変換したもの(`notam_xls_import.py convert`)を、
data_crypt で暗号化して `*.json.enc` で置く(平文の *.json はコミットしない)。
ワークフロー「NOTAM import」(notam-import.yml)が、ここにあるものを全部 data ブランチの notam_out/archive に取り込む
(既にある NOTAM は足さないので、何度実行してもよい)。HANDOFF_NOTAM.md §33。
