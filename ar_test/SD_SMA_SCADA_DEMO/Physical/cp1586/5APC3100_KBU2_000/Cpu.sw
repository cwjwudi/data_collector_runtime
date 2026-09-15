<?xml version="1.0" encoding="utf-8"?>
<?AutomationStudio FileVersion="4.9"?>
<SwConfiguration CpuAddress="SL1" xmlns="http://br-automation.co.at/AS/SwConfiguration">
  <TaskClass Name="Cyclic#1">
    <Task Name="TimeSt" Source="Archive.TimeSt.prg" Memory="UserROM" Language="ANSIC" Debugging="true" />
  </TaskClass>
  <TaskClass Name="Cyclic#2" />
  <TaskClass Name="Cyclic#3" />
  <TaskClass Name="Cyclic#4">
    <Task Name="DataGen" Source="Archive.DataGen.prg" Memory="UserROM" Language="IEC" Debugging="true" />
    <Task Name="DataRev" Source="Archive.DataRev.prg" Memory="UserROM" Language="IEC" Debugging="true" />
    <Task Name="AlarmQuerr" Source="Archive.AlarmQuerry.prg" Memory="UserROM" Language="IEC" Debugging="true" />
    <Task Name="Trend" Source="Archive.Trend.prg" Memory="UserROM" Language="IEC" Debugging="true" />
    <Task Name="ParallelTe" Source="Archive.ParallelTest.prg" Memory="UserROM" Language="IEC" Debugging="true" />
    <Task Name="DataSQLAla" Source="SMA.DataSQLAla.prg" Memory="UserROM" Language="IEC" Debugging="true" />
    <Task Name="DataSQLAud" Source="SMA.DataSQLAud.prg" Memory="UserROM" Language="IEC" Debugging="true" />
    <Task Name="DataSQLRec" Source="SMA.DataSQLRec.prg" Memory="UserROM" Language="IEC" Debugging="true" />
    <Task Name="DataSQLPro" Source="SMA.DataSQLPro.prg" Memory="UserROM" Language="IEC" Debugging="true" />
    <Task Name="DataSQLBat" Source="SMA.DataSQLBat.prg" Memory="UserROM" Language="IEC" Debugging="true" />
    <Task Name="LoadCtrl" Source="SMA.LoadControl.prg" Memory="UserROM" Language="IEC" Debugging="true" />
  </TaskClass>
  <TaskClass Name="Cyclic#5" />
  <TaskClass Name="Cyclic#6" />
  <TaskClass Name="Cyclic#7" />
  <TaskClass Name="Cyclic#8" />
  <Binaries>
    <BinaryObject Name="udbdef" Source="" Memory="UserROM" Language="Binary" />
    <BinaryObject Name="mvLoader" Source="" Memory="UserROM" Language="Binary" />
    <BinaryObject Name="FWRules" Source="" Memory="UserROM" Language="Binary" />
    <BinaryObject Name="TCData" Source="" Memory="SystemROM" Language="Binary" />
    <BinaryObject Name="arsvcreg" Source="" Memory="UserROM" Language="Binary" />
    <BinaryObject Name="TCLang" Source="" Memory="UserROM" Language="Binary" />
    <BinaryObject Name="sysconf" Source="" Memory="SystemROM" Language="Binary" />
    <BinaryObject Name="Role" Source="" Memory="UserROM" Language="Binary" />
    <BinaryObject Name="User" Source="" Memory="UserROM" Language="Binary" />
    <BinaryObject Name="iomap" Source="" Memory="UserROM" Language="Binary" />
    <BinaryObject Name="ashwd" Source="" Memory="SystemROM" Language="Binary" />
    <BinaryObject Name="asfw" Source="" Memory="SystemROM" Language="Binary" />
    <BinaryObject Name="arconfig" Source="" Memory="SystemROM" Language="Binary" />
    <BinaryObject Name="ashwac" Source="" Memory="UserROM" Language="Binary" />
    <BinaryObject Name="TC" Source="" Memory="UserROM" Language="Binary" />
  </Binaries>
  <Libraries>
    <LibraryObject Name="AsHttp" Source="Libraries.AsHttp.lby" Memory="UserROM" Language="binary" Debugging="true" />
    <LibraryObject Name="runtime" Source="Libraries.runtime.lby" Memory="UserROM" Language="binary" Debugging="true" />
    <LibraryObject Name="asstring" Source="Libraries.asstring.lby" Memory="UserROM" Language="binary" Debugging="true" />
    <LibraryObject Name="AsBrStr" Source="Libraries.AsBrStr.lby" Memory="UserROM" Language="binary" Debugging="true" />
    <LibraryObject Name="sys_lib" Source="Libraries.sys_lib.lby" Memory="UserROM" Language="binary" Debugging="true" />
    <LibraryObject Name="LxVar" Source="Libraries.LxVar.lby" Memory="UserROM" Language="ANSIC" Debugging="true" />
    <LibraryObject Name="BrRand" Source="Libraries.BrRand.lby" Memory="UserROM" Language="ANSIC" Debugging="true" />
    <LibraryObject Name="MpAlarm" Source="Libraries.MpAlarm.lby" Memory="UserROM" Language="Binary" Debugging="true" />
    <LibraryObject Name="MpBase" Source="Libraries.MpBase.lby" Memory="UserROM" Language="Binary" Debugging="true" />
    <LibraryObject Name="MpReport" Source="Libraries.MpReport.lby" Memory="UserROM" Language="Binary" Debugging="true" />
    <LibraryObject Name="standard" Source="Libraries.standard.lby" Memory="UserROM" Language="binary" Debugging="true" />
    <LibraryObject Name="arssl" Source="" Memory="UserROM" Language="Binary" Debugging="true" />
    <LibraryObject Name="fileio" Source="" Memory="UserROM" Language="Binary" Debugging="true" />
    <LibraryObject Name="astime" Source="" Memory="UserROM" Language="Binary" Debugging="true" />
    <LibraryObject Name="asieccon" Source="" Memory="UserROM" Language="Binary" Debugging="true" />
    <LibraryObject Name="brsystem" Source="" Memory="UserROM" Language="Binary" Debugging="true" />
  </Libraries>
</SwConfiguration>
