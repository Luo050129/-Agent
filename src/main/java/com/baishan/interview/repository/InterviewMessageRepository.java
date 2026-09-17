package com.baishan.interview.repository;

import com.baishan.interview.domain.InterviewMessage;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;
import java.util.UUID;

public interface InterviewMessageRepository extends JpaRepository<InterviewMessage, Long> {

    List<InterviewMessage> findBySessionIdOrderByIdAsc(UUID sessionId);

    long countBySessionId(UUID sessionId);
}
