package com.example.repro.mapper;

import java.util.List;
import java.util.Map;
import org.apache.ibatis.annotations.*;

@Mapper
public interface BOutCursorMapper {

    // b-out-cursor.sql:7 — PKG_B_OUT_CURSOR.prc_out_cursor
    List<Map<String, Object>> selectPrcOutCursor();
}
