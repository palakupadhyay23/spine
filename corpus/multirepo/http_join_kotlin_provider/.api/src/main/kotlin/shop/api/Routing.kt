package shop.api

import io.ktor.server.application.*
import io.ktor.server.routing.*

fun Application.module() {
    routing {
        route("/v1") {
            get("/topics", ::listTopics)
        }
    }
}

fun listTopics() {}
