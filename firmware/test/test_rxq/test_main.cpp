// Host unit test for rxq — reassembly of 0x01-framed host messages and the
// single-producer queue. No Arduino deps:
//
//   c++ -std=c++17 -I ../../src test_main.cpp ../../src/rxq.cpp -o t && ./t

#include "rxq.h"
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>

static int failures = 0;
#define CHECK(cond)                                                            \
    do {                                                                       \
        if (!(cond)) {                                                         \
            printf("FAIL %s:%d  %s\n", __FILE__, __LINE__, #cond);             \
            ++failures;                                                        \
        }                                                                      \
    } while (0)

static void feed_str(const char* s) { rxq_feed((const uint8_t*)s, strlen(s)); }

// Same split as bridge/protocol.py frame().
static std::vector<std::vector<uint8_t>> frames(const std::string& msg, uint8_t id, size_t chunk) {
    std::vector<std::vector<uint8_t>> out;
    size_t body = chunk - 4, n = (msg.size() + body - 1) / body;
    for (size_t i = 0; i < n; i++) {
        std::vector<uint8_t> f = {0x01, id, (uint8_t)i, (uint8_t)n};
        std::string part = msg.substr(i * body, body);
        f.insert(f.end(), part.begin(), part.end());
        out.push_back(f);
    }
    return out;
}

static void bare_messages_come_out_in_order() {
    rxq_reset();
    feed_str("{\"a\":1}");
    feed_str("{\"a\":2}");
    CHECK(rxq_has());
    CHECK(strcmp(rxq_pop(), "{\"a\":1}") == 0);
    CHECK(strcmp(rxq_pop(), "{\"a\":2}") == 0);
    CHECK(!rxq_has());
    CHECK(rxq_pop() == nullptr);
}

static void framed_message_reassembles() {
    rxq_reset();
    std::string msg = "{\"t\":\"prompt\",\"tx\":\"" + std::string(500, 'x') + "\"}";
    for (auto& f : frames(msg, 7, 64)) rxq_feed(f.data(), f.size());
    CHECK(rxq_has());
    CHECK(msg == rxq_pop());
    CHECK(rxq_dropped() == 0);
}

static void new_id_drops_unfinished_message() {
    rxq_reset();
    auto a = frames(std::string(300, 'a'), 1, 64);
    auto b = frames("{\"ok\":" + std::string(200, ' ') + "1}", 2, 64);
    rxq_feed(a[0].data(), a[0].size());
    for (auto& f : b) rxq_feed(f.data(), f.size());
    CHECK(rxq_has());
    CHECK(strncmp(rxq_pop(), "{\"ok\":", 6) == 0);
    CHECK(!rxq_has());
    CHECK(rxq_dropped() == 1);
}

static void out_of_order_frame_is_dropped() {
    rxq_reset();
    auto a = frames(std::string(300, 'a'), 3, 64);
    rxq_feed(a[0].data(), a[0].size());
    rxq_feed(a[2].data(), a[2].size());
    for (size_t i = 1; i < a.size(); i++) rxq_feed(a[i].data(), a[i].size());
    CHECK(!rxq_has());
    CHECK(rxq_dropped() >= 1);
}

static void full_queue_keeps_older_messages() {
    rxq_reset();
    char buf[16];
    for (int i = 0; i < RXQ_SLOTS + 3; i++) {
        snprintf(buf, sizeof(buf), "{\"n\":%d}", i);
        feed_str(buf);
    }
    CHECK(rxq_dropped() == 4);   // one slot always stays free
    CHECK(strcmp(rxq_pop(), "{\"n\":0}") == 0);
}

static void popped_slot_is_not_reused_while_held() {
    rxq_reset();
    char buf[16];
    for (int i = 0; i < RXQ_SLOTS - 1; i++) { snprintf(buf, sizeof(buf), "{\"n\":%d}", i); feed_str(buf); }
    const char* first = rxq_pop();
    feed_str("{\"n\":99}");      // queue still full while `first` is held
    CHECK(strcmp(first, "{\"n\":0}") == 0);
    CHECK(rxq_dropped() == 1);
}

int main() {
    bare_messages_come_out_in_order();
    framed_message_reassembles();
    new_id_drops_unfinished_message();
    out_of_order_frame_is_dropped();
    full_queue_keeps_older_messages();
    popped_slot_is_not_reused_while_held();
    if (failures) { printf("%d failure(s)\n", failures); return 1; }
    printf("rxq: all tests passed\n");
    return 0;
}
