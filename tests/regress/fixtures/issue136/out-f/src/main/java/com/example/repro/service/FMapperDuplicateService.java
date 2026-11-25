package com.example.repro.service;

import com.example.repro.exception.BusinessException;
import com.example.repro.mapper.FMapperDuplicateMapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
// Source: f-mapper-duplicate.sql
public class FMapperDuplicateService {
    private static final Logger log = LoggerFactory.getLogger(FMapperDuplicateService.class);

    private final FMapperDuplicateMapper fMapperDuplicateMapper;

    public FMapperDuplicateService(FMapperDuplicateMapper fMapperDuplicateMapper) {
        this.fMapperDuplicateMapper = fMapperDuplicateMapper;
    }

    // Source: PKG_F_MAPPER_DUPLICATE.prc_dynamic (PROCEDURE) — f-mapper-duplicate.sql:7-14
    public void prcDynamic(long pId) {
        Long n = 1L;
        String vSql = null;
        Long vValue = 0L;
        vSql = "SELECT 1 FROM dual WHERE 1 = :n";
        fMapperDuplicateMapper.selectPrcDynamic(pId, n);
    }
}
