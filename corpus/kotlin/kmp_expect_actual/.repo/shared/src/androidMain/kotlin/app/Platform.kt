package app

actual class Clock actual constructor() {
    actual fun now(): Long = 0L
}

actual fun platformName(): String = "android"

actual fun orphanPlatform(): String = "none"

fun androidOnly(): String = "helper"
