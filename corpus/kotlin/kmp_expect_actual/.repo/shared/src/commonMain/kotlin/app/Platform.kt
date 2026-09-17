package app

expect class Clock() {
    fun now(): Long
}

expect fun platformName(): String

fun describe(): String = platformName()
