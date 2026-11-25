package com.example.repro.mapper;

import java.util.Map;
import org.apache.ibatis.annotations.*;

@Mapper
public interface FMapperDuplicateMapper {

    // f-mapper-duplicate.sql:7 — PKG_F_MAPPER_DUPLICATE.prc_dynamic
    Map<String, Object> selectPrcDynamic(@Param("pId") Long pId, @Param("n") Long n);
}
