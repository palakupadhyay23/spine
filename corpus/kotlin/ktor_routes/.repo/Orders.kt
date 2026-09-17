package svc

import io.ktor.server.routing.*

fun Route.orders() {
    get("/orders") { }
    route("/orders/{id}") {
        delete { }
    }
}

fun Route.orphan() {
    get("/never-mounted") { }
}
