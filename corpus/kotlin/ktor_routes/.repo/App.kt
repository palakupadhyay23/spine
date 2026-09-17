package svc

import io.ktor.server.application.*
import io.ktor.server.auth.*
import io.ktor.server.routing.*

object Paths {
    const val ADMIN = "/admin"
}

fun Application.module() {
    routing {
        get("/health") { }
        get("/ping", ::pingHandler)
        authenticate("session") {
            get("/me") { }
        }
        route("/api") {
            route("/users") {
                get { }
                post { }
                get("/{id}") { }
            }
        }
        route("/v1") {
            orders()
        }
        route(Paths.ADMIN) {
            get("/secret") { }
        }
        get(buildPath()) { }
    }
}

fun pingHandler() {}

fun buildPath(): String = "/computed"
