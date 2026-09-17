plugins {
    id("shop.android.library")
}

dependencies {
    implementation(project(":core:model"))
    implementation(libs.kotlinx.datetime)
    testImplementation(project(":core:model"))
}
