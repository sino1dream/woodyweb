<?php
// GetSwitchDates 的对照探针：函数体从 php/sql/sqldailyclose.php:215-264
// **现场抽取**，只把取数那段（mysqli_fetch_assoc 循环）换成遍历注入的数组，
// 其余一行不改 —— 这样才能保证"跑的是 PHP 自己的算法"，而不是我重写的版本。
//
// 输入：JSON 文件（argv[1]），形如 {"SZ161125": [{"date":..., "close":...}, ...]}
//       **顺序必须是 date DESC**，与 DailyCloseSql::GetAll() 一致
//       （BuildOrderBy() -> _SqlOrderByDate() = `date DESC`）。
// 输出：stdout 只有一行 JSON（诊断信息走 stderr）。

function GetSwitchDatesFromRecords($arRecords)
{
    $arDate = array();
    $bFirst = true;
    foreach ($arRecords as $record)
    {
        $strDate = $record['date'];
        $fCur = floatval($record['close']);
        if ($bFirst)
        {
            $arDate[] = $strDate;
            $bSecond = true;
            $bFirst = false;
        }
        else
        {
            if ($bSecond)
            {
                $bUp = ($fOld > $fCur) ? true : false;
                $bSecond = false;
            }
            else
            {
                if ($bUp)
                {
                    if ($fOld < $fCur)
                    {
                        $bUp = false;
                        $arDate[] = $strOldDate;
                    }
                }
                else
                {
                    if ($fOld > $fCur)
                    {
                        $bUp = true;
                        $arDate[] = $strOldDate;
                    }
                }
            }
        }
        $fOld = $fCur;
        $strOldDate = $strDate;
    }
    return $arDate;
}

// 状态机推演（只为了对照时肉眼核对，算法本身与上面同一套判断）
function TraceSwitchDates($arRecords)
{
    $arTrace = array();
    $bFirst = true;
    $bSecond = false;
    $bUp = false;
    $fOld = 0.0;
    $strOldDate = '';
    foreach ($arRecords as $record)
    {
        $strDate = $record['date'];
        $fCur = floatval($record['close']);
        $strAction = '';
        if ($bFirst)
        {
            $strAction = '首条入组';
            $bSecond = true;
            $bFirst = false;
        }
        else
        {
            if ($bSecond)
            {
                $bUp = ($fOld > $fCur) ? true : false;
                $bSecond = false;
                $strAction = '定初始方向 bUp='.($bUp ? 'true' : 'false');
            }
            else
            {
                if ($bUp)
                {
                    if ($fOld < $fCur)
                    {
                        $bUp = false;
                        $strAction = '转跌，记 '.$strOldDate.'（极小）';
                    }
                }
                else
                {
                    if ($fOld > $fCur)
                    {
                        $bUp = true;
                        $strAction = '转涨，记 '.$strOldDate.'（极大）';
                    }
                }
            }
        }
        $arTrace[] = array('date' => $strDate, 'close' => $strDate ? $record['close'] : '',
                           'action' => $strAction, 'bUp' => $bUp ? 'true' : 'false');
        $fOld = $fCur;
        $strOldDate = $strDate;
    }
    return $arTrace;
}

$strFile = $argv[1];
$arInput = json_decode(file_get_contents($strFile), true);
if ($arInput === null)
{
    fwrite(STDERR, 'JSON 解析失败：'.$strFile."\n");
    exit(1);
}

$arOut = array();
foreach ($arInput as $strSymbol => $arRecords)
{
    $arOut[$strSymbol] = array(
        'switch_dates' => GetSwitchDatesFromRecords($arRecords),
        'trace' => TraceSwitchDates($arRecords),
    );
}
echo json_encode($arOut);
