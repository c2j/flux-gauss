package com.example.repro.service;

import com.example.repro.exception.BusinessException;
import com.example.repro.mapper.ECollectionMapper;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
// Source: e-collection.sql
public class ECollectionService {
    private static final Logger log = LoggerFactory.getLogger(ECollectionService.class);

    private final ECollectionMapper eCollectionMapper;

    public ECollectionService(ECollectionMapper eCollectionMapper) {
        this.eCollectionMapper = eCollectionMapper;
    }

    public static class Item {
        public Long itemCount;
    }

    // Source: PKG_E_COLLECTION.prc_collection (PROCEDURE) — e-collection.sql:11-22
    public void prcCollection() {
        Long vI = 0L;
        Map<String, Object> vItem = new HashMap<>();
        java.util.List<Long> vNums = new java.util.ArrayList<>();
        vNums = new java.util.ArrayList<Long>();
        vItem.put("itemCount", 2);
        for (vI = 1L; vI <= java.util.Optional.ofNullable((Number) (vItem.getOrDefault("itemCount", vItem.get("item_count")))).map(Number::longValue).orElse(0L); vI++) {
            // SQL: v_nums . EXTEND
            while (vNums.size() < (int)(long)(vI)) { vNums.add(null); }
            vNums.set((int)(long)(vI) - 1, ((Number)(vI)).longValue());
        }
    }
}
